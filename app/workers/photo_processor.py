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
    uploaded_name = None
    
    try:
        # Upload to Gemini directly
        uploaded_name = await ai.upload_media_file(str(file_path))
        
        # Poll processing
        file_status = await ai.get_file_status(uploaded_name)
        while file_status == "PROCESSING":
            await asyncio.sleep(2)
            file_status = await ai.get_file_status(uploaded_name)
            
        if file_status == "FAILED":
            log.error("gemini_processing_failed")
            return
            
        # Analyze
        analysis = await ai.analyze_roof_photo(uploaded_name, filename, job_id)
        
        # Save to SQLite cache so it is immediately available
        from app.core.inspection_models import _compute_sha256
        from app.core.cache import set_cached_analysis
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
        log.info("photo_damage_analysis_complete", signal=signal)
        
    except Exception as e:
        log.error("photo_damage_analysis_error", error=str(e))
    finally:
        if uploaded_name:
            try:
                await ai.delete_file(uploaded_name)
            except Exception:
                pass
