import os
from pathlib import Path
import pytest

from app.core.backup import backup_database
from app.core.database import get_db_path

def test_backup_database_coverage():
    """Test backup_database creates a database copy and cleans up old backups."""
    db_path = get_db_path()
    backup_dir = db_path.parent / "backups"
    
    # Run backup with a retention of 1 day (max 4 files)
    backup_database(retention_days=1)
    
    # Assert backup directory and backup files exist
    assert backup_dir.exists()
    backup_files = list(backup_dir.glob(f"{db_path.stem}_*.db"))
    assert len(backup_files) > 0
    
    # Run it multiple times to trigger pruning logic
    for _ in range(5):
         backup_database(retention_days=1)
         
    # Check that it pruned oldest backups
    backup_files_after = list(backup_dir.glob(f"{db_path.stem}_*.db"))
    assert len(backup_files_after) <= 4

    # Cleanup the backups created
    for f in backup_files_after:
        try:
            f.unlink()
        except OSError:
            pass
