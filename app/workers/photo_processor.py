import asyncio
import json

import structlog

from app.api.field_routes import FIELD_PHOTOS_DIR
from app.core.database import get_connection
from app.services.ai_service import get_ai_client

logger = structlog.get_logger("app.workers.photo_processor")

def _sync_update_damage_signals(job_id: str, new_signal: dict):
    """
    Append a new damage signal to the job's damage_signals JSON column.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if not row:
            return
            
        try:
            signals = json.loads(row["damage_signals"]) if row["damage_signals"] else []
        except Exception:
            signals = []
            
        new_filename = new_signal.get("filename")
        updated = False
        if new_filename:
            for idx, existing in enumerate(signals):
                if isinstance(existing, dict) and existing.get("filename") == new_filename:
                    signals[idx] = new_signal
                    updated = True
                    break

        if not updated:
            signals.append(new_signal)
        
        conn.execute(
            "UPDATE jobs SET damage_signals = ? WHERE id = ?",
            (json.dumps(signals), job_id)
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()

async def process_photo_damage(ctx: dict, job_id: str, filename: str) -> None:
    """
    ARQ task triggered when a field rep uploads a photo.
    Analyzes the photo for damage using Gemini Vision.
    """
    log = logger.bind(job_id=job_id, photo=filename)
    log.info("photo_damage_analysis_started")
    
    file_path = FIELD_PHOTOS_DIR / job_id / filename
    if not file_path.exists():
        log.error("photo_file_missing")
        return
        
    ai = get_ai_client()
    
    try:
        # Analyze using direct local file path
        analysis = await ai.analyze_roof_photo(file_path, filename, job_id)
        
        # Save to SQLite cache so it is immediately available
        from app.core.cache import set_cached_analysis
        from app.core.inspection_models import _compute_sha256
        sha = _compute_sha256(file_path)
        await asyncio.to_thread(set_cached_analysis, job_id, sha, analysis)
        
        # Build damage signal
        confidence = analysis.confidence
        damage_type = analysis.damage_type.value
        
        needs_review = False
        if confidence < 0.70:
            damage_type = "unknown"
            needs_review = True
            
        signal = {
            "damage_type": damage_type,
            "confidence": confidence,
            "source": "gemini_v2_vision",
            "needs_review": needs_review,
            "filename": filename,
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        }
        
        await asyncio.to_thread(_sync_update_damage_signals, job_id, signal)
        
        # Ensure status is advanced to PHOTOS_UPLOADED if job is still in pre-photo stage
        def _ensure_photos_uploaded():
            conn = get_connection()
            try:
                from app.core.database import JobStatus, update_job_status
                row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row and row["status"] in (
                    JobStatus.LEAD_CAPTURED.value,
                    JobStatus.CONTINGENCY_SIGNED.value,
                    JobStatus.RETAIL_CONTRACT_SIGNED.value,
                ):
                    update_job_status(
                        job_id,
                        JobStatus.PHOTOS_UPLOADED,
                        f"Photo damage analysis complete: {filename}",
                    )
            finally:
                conn.close()

        await asyncio.to_thread(_ensure_photos_uploaded)
        log.info("photo_damage_analysis_complete", signal=signal)
        
    except Exception as e:
        log.error("photo_damage_analysis_error", error=str(e))
