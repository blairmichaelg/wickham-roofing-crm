"""
Artifact Pruning Utility (Garbage Collection).

Scans directories to delete temporary files older than 24 hours that are not
securely referenced in the database.
"""

import time
from pathlib import Path

import structlog

from app.core.database import get_connection

logger = structlog.get_logger("app.core.cleanup")

DIRECTORIES_TO_SCAN = [
    Path("field_photos"),
    Path("field_docs"),
    Path("signed_agreements"),
    Path("generated_exports"),
    Path("data/field_docs"),
]

def cleanup_orphaned_artifacts() -> None:
    """
    Deletes files older than 24 hours that are NOT securely referenced in
    the jobs or job_documents database tables.
    """
    logger.info("artifact_cleanup_started")
    now = time.time()
    twenty_four_hours_ago = now - (24 * 60 * 60)

    # 1. Fetch valid job_ids and storage_paths
    valid_job_ids = set()
    valid_storage_paths = set()
    
    conn = get_connection()
    try:
        jobs_cursor = conn.execute("SELECT id FROM jobs")
        valid_job_ids = {row["id"] for row in jobs_cursor}

        docs_cursor = conn.execute("SELECT storage_path FROM job_documents")
        valid_storage_paths = {Path(row["storage_path"]).resolve() for row in docs_cursor if row["storage_path"]}
    except Exception as e:
        logger.error("failed_to_fetch_cleanup_references", error=str(e))
        return
    finally:
        conn.close()

    deleted_count = 0
    failed_count = 0

    # 2. Walk directories and prune
    for directory in DIRECTORIES_TO_SCAN:
        if not directory.exists():
            continue
            
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                try:
                    stat = file_path.stat()
                    if stat.st_mtime < twenty_four_hours_ago:
                        # Check if absolute path is securely referenced in job_documents
                        abs_path = file_path.resolve()
                        if abs_path in valid_storage_paths:
                            continue
                            
                        # Check if any part of the path contains a valid job_id
                        path_parts = set(file_path.parts)
                        # Also check the filename specifically in case it's formatted like 'JOB_ID_signature.png'
                        file_name = file_path.name
                        
                        is_referenced = False
                        for job_id in valid_job_ids:
                            if job_id in path_parts or job_id in file_name:
                                is_referenced = True
                                break
                                
                        if not is_referenced:
                            try:
                                file_path.unlink()
                                deleted_count += 1
                                logger.debug("orphaned_artifact_deleted", path=str(file_path))
                            except Exception as e:
                                failed_count += 1
                                logger.warning("orphaned_artifact_deletion_failed", path=str(file_path), error=str(e))
                except Exception as e:
                    logger.warning("failed_to_stat_file", path=str(file_path), error=str(e))

    logger.info("artifact_cleanup_complete", deleted=deleted_count, failed=failed_count)
