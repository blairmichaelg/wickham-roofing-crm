import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0004_add_commission_overrides")

def up(conn: sqlite3.Connection) -> None:
    """Apply the migration to add commission_pct_override and ACV/Supplement check tracking to jobs table."""
    logger.info("applying_migration", version=4, name="add_commission_overrides")
    
    columns = [
        "commission_pct_override REAL DEFAULT NULL",
        "acv_check_amount REAL DEFAULT NULL",
        "acv_check_date TEXT DEFAULT NULL",
        "supplement_check_amount REAL DEFAULT NULL",
        "supplement_check_date TEXT DEFAULT NULL"
    ]
    
    for col in columns:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col};")
        except sqlite3.OperationalError as e:
            # Ignore if columns already exist
            if "duplicate column name" not in str(e).lower():
                raise
