import sqlite3

import structlog

logger = structlog.get_logger(__name__)

def up(conn: sqlite3.Connection) -> None:
    """Apply the commission_ready migration."""
    logger.info("running_migration", version="0008_commission_ready")
    
    try:
        cursor = conn.execute("PRAGMA table_info(jobs)")
        columns = [row["name"] for row in cursor.fetchall()]
        
        if "commission_ready" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN commission_ready BOOLEAN DEFAULT 0")
        
        if "commission_generated_at" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN commission_generated_at TEXT")
            
        if "commission_pct_override" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN commission_pct_override REAL")
            
    except Exception as e:
        logger.error("migration_failed", version="0008", error=str(e))
        raise
