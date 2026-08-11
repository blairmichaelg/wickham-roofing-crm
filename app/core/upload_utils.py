import hashlib
from pathlib import Path

import structlog
from fastapi import HTTPException, UploadFile

logger = structlog.get_logger("app.core.upload_utils")

async def stream_upload_safely(file: UploadFile, dest_path: Path, max_bytes: int = 10 * 1024 * 1024, allowed_magic_bytes: list[bytes] | None = None) -> str:
    """
    Safely stream an UploadFile to disk in chunks to strictly enforce file size limits 
    without causing MemoryError (OOM) or blocking the async event loop.
    Supports optional magic-number byte inspection for strict MIME validation.
    Returns the SHA-256 hash of the uploaded file.
    """
    # 1. Fast-fail if the native Starlette spool size is already calculated and oversized
    if getattr(file, 'size', 0) and file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large ({max_bytes // (1024 * 1024)}MB max).")

    bytes_written = 0
    hasher = hashlib.sha256()
    try:
        import asyncio
        # 2. Open file in threadpool to prevent blocking the event loop
        buffer = await asyncio.to_thread(dest_path.open, "wb")
        try:
            # 3. Read in strict 1MB chunks to keep RAM footprint low and deterministic
            first_chunk = True
            while chunk := await file.read(1024 * 1024):
                if first_chunk and allowed_magic_bytes:
                    if not any(chunk.startswith(magic) for magic in allowed_magic_bytes):
                        raise HTTPException(status_code=400, detail="Invalid file type signature (magic number mismatch).")
                first_chunk = False
                
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File too large ({max_bytes // (1024 * 1024)}MB max).")
                hasher.update(chunk)
                # 4. Write to disk via threadpool to maintain concurrency
                await asyncio.to_thread(buffer.write, chunk)
        finally:
            await asyncio.to_thread(buffer.close)
            
        return hasher.hexdigest()
    except Exception:
        # 3. Aggressive cleanup on any interruption (network drop, timeout, or size limit)
        dest_path.unlink(missing_ok=True)
        raise
