import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0006_add_damage_and_storm_events")

def up(conn: sqlite3.Connection) -> None:
    """
    Migration to add damage_signals and storm_events tables.
    """
    logger.info("running_migration", version="0006")
    
    # Try adding damage_signals column (it might exist if script ran previously)
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN damage_signals TEXT DEFAULT '[]'")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            pass
        else:
            raise

    # Create storm_events table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS storm_events (
            id TEXT PRIMARY KEY,
            zipcode TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_date TIMESTAMP NOT NULL,
            hail_size_inches REAL,
            wind_speed_mph REAL,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create canvassing target index
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_storm_events_zipcode 
        ON storm_events(zipcode)
    ''')
