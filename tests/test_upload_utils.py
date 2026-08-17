from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from app.core.upload_utils import stream_upload_safely


@pytest.mark.asyncio
async def test_stream_upload_safely_exceeds_max_bytes(tmp_path: Path):
    dest_path = tmp_path / "test_upload.pdf"
    
    # Mock UploadFile
    mock_file = MagicMock(spec=UploadFile)
    mock_file.size = None
    
    # Simulate reading chunks: three 5MB chunks (total 15MB > 10MB)
    chunk_data = b"0" * (5 * 1024 * 1024)
    mock_file.read = AsyncMock(side_effect=[chunk_data, chunk_data, chunk_data, b""])
    
    with pytest.raises(HTTPException) as exc_info:
        await stream_upload_safely(mock_file, dest_path, max_bytes=10 * 1024 * 1024)
        
    assert exc_info.value.status_code == 413
    assert "File too large" in str(exc_info.value.detail)
    
    # Verify the partial file was unlinked
    assert not dest_path.exists()

@pytest.mark.asyncio
async def test_stream_upload_safely_fast_fail(tmp_path: Path):
    dest_path = tmp_path / "test_upload_fast.pdf"
    
    # Mock UploadFile with size populated by Starlette
    mock_file = MagicMock(spec=UploadFile)
    mock_file.size = 15 * 1024 * 1024
    
    with pytest.raises(HTTPException) as exc_info:
        await stream_upload_safely(mock_file, dest_path, max_bytes=10 * 1024 * 1024)
        
    assert exc_info.value.status_code == 413
    
    # Verify the file was never created
    assert not dest_path.exists()

@pytest.mark.asyncio
async def test_stream_upload_safely_success(tmp_path: Path):
    dest_path = tmp_path / "test_upload_success.pdf"
    
    mock_file = MagicMock(spec=UploadFile)
    mock_file.size = 5 * 1024 * 1024
    chunk_data = b"1" * (5 * 1024 * 1024)
    mock_file.read = AsyncMock(side_effect=[chunk_data, b""])
    
    await stream_upload_safely(mock_file, dest_path, max_bytes=10 * 1024 * 1024)
    
    assert dest_path.exists()
    assert dest_path.stat().st_size == 5 * 1024 * 1024
