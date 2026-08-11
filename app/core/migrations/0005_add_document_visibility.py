import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0005_add_document_visibility")

def up(conn: sqlite3.Connection) -> None:
    """Add visibility and category to job_documents."""
    logger.info("applying_migration", version=5, name="add_document_visibility")
    
    # 1. Add visibility column defaulting to office_only for safety
    try:
        conn.execute("ALTER TABLE job_documents ADD COLUMN visibility TEXT NOT NULL DEFAULT 'office_only'")
    except sqlite3.OperationalError:
        pass # Column already exists
        
    # 2. Add category column
    try:
        conn.execute("ALTER TABLE job_documents ADD COLUMN category TEXT DEFAULT 'UNSPECIFIED'")
    except sqlite3.OperationalError:
        pass
        
    # 3. Backfill any existing documents that should be field_safe
    # Examples: Photos, EagleView, Contingency
    conn.execute("""
        UPDATE job_documents
        SET visibility = 'field_safe'
        WHERE file_type IN ('PHOTO', 'EAGLEVIEW_PDF', 'CONTINGENCY_SIGNED')
           OR file_type LIKE 'image/%'
    """)
    
    logger.info("migration_complete", version=5)
