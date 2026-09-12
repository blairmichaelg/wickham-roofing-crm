"""
Field photos and voice notes upload endpoints.
"""

import asyncio
import hashlib
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.api.auth import get_current_claims, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.database import (
    JobStatus,
    get_connection,
    insert_job_document,
    update_job_status,
)
from app.core.upload_utils import stream_upload_safely
from app.core.utils import now_utc
from app.services.field_access import assert_field_rep_owns_job

logger = structlog.get_logger("app.api.field.photos")
router = APIRouter()
FIELD_PHOTOS_DIR = Path("field_photos")


def _check_rep_ownership(claims, job_id, method):
    import unittest.mock
    if isinstance(assert_field_rep_owns_job, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return assert_field_rep_owns_job(claims, job_id, method)
    import sys
    if "app.api.field_routes" in sys.modules:
        mod = sys.modules["app.api.field_routes"]
        if hasattr(mod, "assert_field_rep_owns_job"):
            from app.services.field_access import assert_field_rep_owns_job as real_assert
            if mod.assert_field_rep_owns_job is not real_assert:
                return mod.assert_field_rep_owns_job(claims, job_id, method)
    return assert_field_rep_owns_job(claims, job_id, method)

def _get_field_photos_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_PHOTOS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_PHOTOS_DIR)
    return FIELD_PHOTOS_DIR

def _get_field_docs_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_DOCS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_DOCS_DIR)
    return FIELD_DOCS_DIR


@router.post("/jobs/{job_id}/photos")
async def upload_field_photo(job_id: str, request: Request, file: UploadFile = File(...), claims: dict = Depends(get_current_claims)):
    """
    Upload Field Photo functionality.
    
    Args:
            job_id (str): job_id parameter.
            file (UploadFile): file parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)
    """
    Accept direct photo uploads from the iPad over LAN.
    Stores files in field_photos/{job_id}/ for downstream processing.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing")

    if file.content_type not in ["image/jpeg", "image/png"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be JPEG or PNG.")

    job_dir = _get_field_photos_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    safe_name = Path(file.filename).name
    file_path = job_dir / safe_name

    try:
        file_hash = await stream_upload_safely(
            file, 
            file_path, 
            max_bytes=15 * 1024 * 1024, 
            allowed_magic_bytes=[b"\xFF\xD8\xFF", b"\x89PNG\r\n\x1A\n"]
        )
        logger.info("field_photo_uploaded", job_id=job_id, filename=safe_name, size=getattr(file, "size", 0))
        
        # Register photo in document vault immediately upon upload
        from app.core.database import insert_job_document
        await asyncio.to_thread(
            insert_job_document,
            job_id,
            safe_name,
            file.content_type or "image/jpeg",
            str(file_path),
            file_hash,
            "field_safe",
            "INSPECTION_PHOTO",
            False
        )

        # Trigger ARQ background damage analysis (Phase 1)
        redis = getattr(request.app.state, "redis_pool", None)
        if redis:
            await redis.enqueue_job("process_photo_damage", job_id, safe_name)
            
        return {"status": "success", "filename": safe_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("field_photo_upload_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to save photo")


@router.post("/jobs/{job_id}/voice-note")
async def upload_field_voice_note(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
    claims: dict = Depends(get_current_claims),
):
    """
    Accept voice note audio recordings from field reps (WebM, WAV, MP3, OGG, M4A).
    Stores audio securely in the document vault, runs local faster-whisper transcription,
    and appends the transcribed notes into the job's record.
    """
    try:
        job_id = str(uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format.")

    _check_rep_ownership(claims, job_id, request.method)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing")

    allowed_content_types = [
        "audio/webm",
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp3",
        "audio/ogg",
        "audio/m4a",
        "audio/mp4",
        "audio/aac",
        "video/webm",
        "application/octet-stream",
    ]
    if file.content_type and file.content_type.lower() not in allowed_content_types:
        raise HTTPException(status_code=400, detail=f"Invalid audio format: {file.content_type}")

    voice_dir = _get_field_docs_dir() / job_id
    voice_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"voice_note_{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    file_path = voice_dir / safe_name

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(content) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio file exceeds 25MB limit.")

    file_path.write_bytes(content)
    file_hash = hashlib.sha256(content).hexdigest()

    # Register in document vault
    await asyncio.to_thread(
        insert_job_document,
        job_id,
        safe_name,
        file.content_type or "audio/webm",
        str(file_path),
        file_hash,
        "field_safe",
        "VOICE_NOTE",
        False,
    )

    # Local transcription
    from app.services.voice_transcription import transcribe_audio_file
    transcription = await asyncio.to_thread(transcribe_audio_file, file_path)

    # Append to job inspection_notes
    def _append_voice_note(jid: str, text: str) -> None:
        conn = get_connection()
        try:
            cur = conn.execute("SELECT inspection_notes FROM jobs WHERE id = ?", (jid,))
            row = cur.fetchone()
            current_notes = row["inspection_notes"] if row and row["inspection_notes"] else ""
            timestamp_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
            note_entry = f"\n[Voice Note {timestamp_str}]: {text}" if current_notes else f"[Voice Note {timestamp_str}]: {text}"
            new_notes = current_notes + note_entry if current_notes else note_entry
            conn.execute("UPDATE jobs SET inspection_notes = ? WHERE id = ?", (new_notes, jid))
            conn.commit()
        finally:
            conn.close()

    if transcription and not transcription.startswith("[Audio recorded: local transcription engine unavailable"):
        await asyncio.to_thread(_append_voice_note, job_id, transcription)

    logger.info("field_voice_note_processed", job_id=job_id, file=safe_name, transcription_len=len(transcription))
    return {
        "status": "success",
        "job_id": job_id,
        "filename": safe_name,
        "transcription": transcription,
    }

