import datetime
import glob
import os
import sqlite3

import structlog

from app.core.database import get_db_path

logger = structlog.get_logger("app.core.backup")

def backup_database(retention_days: int = 14) -> None:
    """
    Creates a safe, live backup of the SQLite database using the native backup API.
    Enforces a rolling retention window to prevent disk bloat.
    """
    db_path = get_db_path()
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = db_path.stem
    backup_file = backup_dir / f"{db_name}_{timestamp}.db"
    
    logger.info("backup_started", target=str(backup_file))
    
    try:
        # Use native SQLite backup API which safely locks the WAL
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_file)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        logger.info("backup_completed", target=str(backup_file))
    except sqlite3.Error as e:
        logger.error("backup_sqlite_error", error=str(e))
        raise
    except OSError as e:
        logger.error("backup_os_error", error=str(e), details="Possible disk full or permissions issue")
        raise
    except Exception as e:
        logger.error("backup_failed", error=str(e))
        raise
        
    # Enforce rolling window
    try:
        db_name = db_path.stem
        all_backups = glob.glob(str(backup_dir / f"{db_name}_*.db"))
        # Sort oldest to newest
        all_backups.sort(key=os.path.getmtime)
        
        # If we have more than retention_days (assuming 1 backup per day or per X hours, actually retention is number of files for safety, or based on time)
        # We will keep the last `retention_days` backups. If run every 6 hours, 14 days = 56 files.
        # Let's say keep 56 files.
        max_files = retention_days * 4 # Assuming every 6 hours
        
        if len(all_backups) > max_files:
            to_delete = all_backups[:-max_files]
            for old_backup in to_delete:
                os.remove(old_backup)
                logger.info("backup_pruned", pruned=old_backup)
    except Exception as e:
        logger.error("backup_pruning_failed", error=str(e))
