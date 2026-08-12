"""
Inspection Vision Engine orchestrator.

Processes a batch of roof photos for a single InspectionJob by:
1. Iterating photos SEQUENTIALLY (no parallelism — free-tier quota protection).
2. Uploading each photo via Gemini File API.
3. Polling until processing completes.
4. Running multimodal damage analysis with the flat PhotoAnalysis schema.
5. Immediately deleting the remote file for privacy and quota management.
6. Providing a Pillow-based image resizer for downstream ReportLab PDF embedding.

This worker follows the same async-to-thread pattern as supplement_processor.py.
"""

import asyncio
import io
import traceback
from pathlib import Path

import structlog
from arq.worker import Retry
from PIL import Image as PILImage

from app.api.field_routes import get_inspection_summary
from app.config import FIELD_DOCS_DIR, get_settings
from app.core.cache import get_cached_analysis, set_cached_analysis
from app.core.database import JobStatus, insert_job_document, update_job_status
from app.core.inspection_models import InspectionJob
from app.core.temp_manager import create_temp_file
from app.services.ai_service import get_ai_client
from app.services.pdf import PDFGenerator

logger = structlog.get_logger("app.workers.inspection_processor")


def resize_for_pdf(src: Path, max_width: int = 800) -> io.BytesIO:
    """
    Downsample an image to a maximum width for safe ReportLab PDF embedding.

    Full-resolution field photos (4000x3000px+) will cause Out-Of-Memory
    crashes when ReportLab builds the platypus story. This function
    produces a lightweight PNG buffer that ReportLab can consume via
    ImageReader without OOM risk.

    Args:
        src: Path to the source image file on disk.
        max_width: Maximum pixel width for the output. Default 800.

    Returns:
        A BytesIO buffer containing the resized PNG image, seeked to 0.
    """
    if max_width == 800:
        max_width = get_settings().pdf_image_max_width

    with PILImage.open(src) as img:
        # Convert HEIC/other modes to RGB for PNG compatibility
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), PILImage.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf


def resize_for_ai(src: Path, max_width: int = 1600) -> str:
    """
    Downsample a field photo for Gemini File API upload.

    Reduces 4000px+ raw field photos to 1600px to save network bandwidth
    and API processing time, while preserving enough detail for forensic
    damage analysis.

    Writes the output to a managed temporary file that will be cleaned up
    on process exit by temp_manager.

    Args:
        src: Path to the source image file on disk.
        max_width: Maximum pixel width for the output. Default 1600.

    Returns:
        Absolute filepath to the downscaled temporary JPEG file.
    """
    if max_width == 1600:
        max_width = get_settings().ai_image_max_width
        
    with PILImage.open(src) as img:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), PILImage.Resampling.LANCZOS)

        temp_path = create_temp_file(suffix=".jpg")
        img.save(temp_path, format="JPEG", quality=85)
        return temp_path


async def process_inspection(ctx: dict, job_id: str) -> InspectionJob:
    """
    Process all photos in an InspectionJob through the Gemini Vision Engine.

    Iterates SEQUENTIALLY to respect free-tier rate limits. Each photo
    goes through the full lifecycle: upload → poll → analyze → delete.

    The _call_with_backoff wrapper on AIService handles 429 retries
    with exponential backoff + jitter.

    Args:
        ctx: Worker context dict (for future CRM client injection).
        job_id: ID of the job to process.

    Returns:
        The updated InspectionJob with analyses populated.
    """
    job_try = ctx.get('job_try', 1)
    log = logger.bind(job_id=job_id, try_num=job_try)
    try:
        job = await get_inspection_summary(job_id, claims={"role": "admin"})

        log = log.bind(total_photos=len(job.photos))
        log.info("inspection_processing_started")

        ai = get_ai_client()

        non_cached_photos = []
        for idx, photo in enumerate(job.photos):
            cached = await asyncio.to_thread(get_cached_analysis, job.job_id, photo.sha256)
            if cached:
                cached.filename = photo.filepath.name
                job.analyses.append(cached)
                log.info("photo_analysis_cache_hit", photo=photo.filepath.name, damage=cached.damage_detected)
            else:
                non_cached_photos.append(photo)

        if non_cached_photos:
            log.info("batch_processing_non_cached_photos", count=len(non_cached_photos))
            
            ai_file_paths: list[str | Path] = []
            try:
                # 1. Rescale photos locally
                for photo in non_cached_photos:
                    log.debug("downscaling_for_ai", photo=photo.filepath.name)
                    ai_file_path = await asyncio.to_thread(resize_for_ai, photo.filepath, max_width=1600)
                    ai_file_paths.append(ai_file_path)
                
                # 2. Call batch analysis
                log.info("calling_batch_photo_analysis", count=len(ai_file_paths))
                batch_analyses = await ai.analyze_roof_photos_batch(
                    file_paths=ai_file_paths,
                    original_filenames=[p.filepath.name for p in non_cached_photos],
                    job_id=job.job_id
                )
                
                # 3. Map results and cache
                analysis_by_filename = {a.filename: a for a in batch_analyses}
                for photo in non_cached_photos:
                    analysis = analysis_by_filename.get(photo.filepath.name)
                    if not analysis:
                        log.warning("batch_analysis_filename_mismatch", expected=photo.filepath.name)
                        try:
                            idx = non_cached_photos.index(photo)
                            if idx < len(batch_analyses):
                                analysis = batch_analyses[idx]
                        except Exception:
                            pass
                    
                    if not analysis:
                        log.error("photo_analysis_result_missing", photo=photo.filepath.name)
                        continue
                    
                    analysis.filename = photo.filepath.name
                    job.analyses.append(analysis)
                    await asyncio.to_thread(set_cached_analysis, job.job_id, photo.sha256, analysis)
                    log.info(
                        "photo_analysis_complete_batch",
                        photo=photo.filepath.name,
                        damage=analysis.damage_detected,
                        severity=analysis.severity.value,
                        confidence=analysis.confidence,
                    )
            except Exception as batch_err:
                log.error("batch_photo_analysis_failed", error=str(batch_err))
            finally:
                for path in ai_file_paths:
                    try:
                        Path(path).unlink(missing_ok=True)
                    except Exception:
                        pass

        log.info(
            "inspection_processing_complete",
            analyzed=len(job.analyses),
            damage_found=job.damage_count,
            actionable=job.has_actionable_damage,
        )

        if not ctx.get("is_test"):
            # Look for signature
            sig_path_c = FIELD_DOCS_DIR / job_id / f"{job_id}_contingency_sig.png"
            sig_path_r = FIELD_DOCS_DIR / job_id / f"{job_id}_retail_contract_sig.png"
            if sig_path_c.exists():
                signature_to_pass = str(sig_path_c)
            elif sig_path_r.exists():
                signature_to_pass = str(sig_path_r)
            else:
                signature_to_pass = None

            # Generate Evidence Grid
            pdf_gen = PDFGenerator()
            pdf_path = await pdf_gen.generate_evidence_grid(job, signature_to_pass)
            
            # Vault the document and update status (Threaded)
            await asyncio.to_thread(insert_job_document, job_id, "evidence_grid.pdf", "application/pdf", pdf_path, None, "field_safe", "EVIDENCE_GRID", True)

            # Generate homeowner-facing inspection report (separate from internal evidence grid)
            try:
                from app.services.pdf.inspection_report import InspectionReportGenerator
                hr_gen = InspectionReportGenerator()
                hr_path = await hr_gen.generate_homeowner_report(job)
                hr_filename = Path(hr_path).name
                await asyncio.to_thread(
                    insert_job_document,
                    job_id, hr_filename, "application/pdf",
                    hr_path, None, "field_safe", "HOMEOWNER_INSPECTION_REPORT", True  # replace_existing=True
                )
                log.info("homeowner_report_generated", path=hr_path, filename=hr_filename)
            except Exception as hr_err:
                log.error("homeowner_report_generation_failed", error=str(hr_err))
                # Non-fatal — do not block INSPECTION_COMPLETED transition

            # Fetch current status of the job from the DB
            from app.core.database import get_connection
            conn = get_connection()
            current_status = None
            try:
                cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
                row = cursor.fetchone()
                if row:
                    current_status = row["status"]
            finally:
                conn.close()

            pre_build_statuses = {
                JobStatus.LEAD_CAPTURED,
                JobStatus.CONTINGENCY_SIGNED,
                JobStatus.RETAIL_CONTRACT_SIGNED,
                JobStatus.CLAIM_FILED,
                JobStatus.PHOTOS_UPLOADED,
                JobStatus.EV_PARSED,
                JobStatus.STATEMENT_OF_LOSS_RECEIVED,
                JobStatus.PENDING_OPERATOR_REVIEW,
                JobStatus.SUPPLEMENT_GENERATED,
                JobStatus.SUPPLEMENT_SUBMITTED,
                JobStatus.SUPPLEMENT_DENIED,
                JobStatus.SUPPLEMENT_APPROVED,
                JobStatus.SCOPE_APPROVED,
                JobStatus.MATERIAL_ORDERED,
                JobStatus.MATERIALS_ON_SITE,
                JobStatus.INSTALL_SCHEDULED,
                JobStatus.AWAITING_CARRIER_RESPONSE,
            }

            if current_status not in pre_build_statuses:
                await asyncio.to_thread(update_job_status, job_id, JobStatus.INSPECTION_COMPLETED)
            else:
                log.info("skipping_status_progression_for_pre_build_job", current_status=current_status)

        return job
    except Exception as e:
        error_msg = str(e)
        log.error("inspection_processing_failed", error=error_msg, try_num=job_try)
        
        is_transient = "timeout" in error_msg.lower() or "429" in error_msg or "connection" in error_msg.lower()
        if is_transient and job_try < 3:
            log.info("retrying_inspection_processing", defer=job_try * 10)
            raise Retry(defer=job_try * 10)

        error_trace = traceback.format_exc()
        if not ctx.get("is_test"):
            try:
                from app.core.database import get_connection
                await asyncio.to_thread(update_job_status, job_id, JobStatus.INSPECTION_FAILED, note=error_msg[:200])
                
                def _log_task_error():
                    conn = get_connection()
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        conn.execute(
                            "INSERT INTO job_tasks (job_id, task_type, phase, last_error) VALUES (?, ?, ?, ?)",
                            (job_id, "INSPECTION_VISION", "ANALYSIS", error_trace)
                        )
                        conn.execute("COMMIT")
                    except Exception:
                        conn.execute("ROLLBACK")
                    finally:
                        conn.close()
                await asyncio.to_thread(_log_task_error)
            except Exception as db_err:
                log.error("failed_to_log_inspection_failure", error=str(db_err))
        raise
