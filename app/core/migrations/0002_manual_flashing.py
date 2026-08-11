import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0002_manual_flashing")

def up(conn: sqlite3.Connection) -> None:
    """Apply the migration to add flashing_lf and step_flashing_lf to jobs table."""
    logger.info("applying_migration", version=2, name="manual_flashing")
    
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN flashing_lf REAL;")
        conn.execute("ALTER TABLE jobs ADD COLUMN step_flashing_lf REAL;")
    except sqlite3.OperationalError as e:
        # Ignore if columns already exist
        if "duplicate column name" not in str(e).lower():
            raise
