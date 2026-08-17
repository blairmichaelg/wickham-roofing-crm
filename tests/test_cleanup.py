"""
Unit tests for the Artifact Garbage Collection (cleanup.py) logic.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.cleanup import DIRECTORIES_TO_SCAN, cleanup_orphaned_artifacts


@pytest.fixture
def mock_directories(tmp_path):
    """Set up temporary directories to match DIRECTORIES_TO_SCAN."""
    dirs = []
    for d in DIRECTORIES_TO_SCAN:
        p = tmp_path / d.name
        p.mkdir(parents=True, exist_ok=True)
        dirs.append(p)
    return dirs

@patch("app.core.cleanup.DIRECTORIES_TO_SCAN")
@patch("app.core.cleanup.get_connection")
def test_cleanup_orphaned_artifacts(mock_get_conn, mock_scan_dirs, mock_directories, tmp_path):
    # Overwrite the global list in the test context
    mock_scan_dirs.__iter__.return_value = mock_directories
    
    # 1. Setup mock database records
    mock_conn = MagicMock()
    mock_get_conn.return_value = mock_conn
    
    def execute_side_effect(query):
        mock_cursor = MagicMock()
        if "FROM jobs" in query:
            # Active job ID
            mock_cursor.__iter__.return_value = [{"id": "ACTIVE-JOB-123"}]
        elif "FROM job_documents" in query:
            # Valid absolute path in job_documents
            valid_path = str((mock_directories[1] / "tracked_doc.pdf").resolve())
            mock_cursor.__iter__.return_value = [{"storage_path": valid_path}]
        return mock_cursor
        
    mock_conn.execute.side_effect = execute_side_effect
    
    # 2. Create some files in the temp dirs
    now = time.time()
    old_time = now - (25 * 60 * 60) # 25 hours ago
    new_time = now - (10 * 60 * 60) # 10 hours ago
    
    # File A: Old, orphaned -> SHOULD BE DELETED
    file_a = mock_directories[0] / "orphaned_photo.jpg"
    file_a.write_text("a")
    import os
    os.utime(file_a, (old_time, old_time))
    
    # File B: Old, but matches active job ID in filename -> KEEP
    file_b = mock_directories[2] / "ACTIVE-JOB-123_signature.png"
    file_b.write_text("b")
    os.utime(file_b, (old_time, old_time))
    
    # File C: Old, but exactly in job_documents -> KEEP
    file_c = mock_directories[1] / "tracked_doc.pdf"
    file_c.write_text("c")
    os.utime(file_c, (old_time, old_time))
    
    # File D: New, orphaned -> KEEP (too young)
    file_d = mock_directories[3] / "new_orphan.csv"
    file_d.write_text("d")
    os.utime(file_d, (new_time, new_time))
    
    # 3. Run cleanup
    cleanup_orphaned_artifacts()
    
    # 4. Assertions
    assert not file_a.exists(), "File A should have been deleted"
    assert file_b.exists(), "File B should be kept (job_id match)"
    assert file_c.exists(), "File C should be kept (job_documents match)"
    assert file_d.exists(), "File D should be kept (too young)"
