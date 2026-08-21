"""Migration 0015: Add ev_ordered_at, adjuster_meeting_at, claim_filed_at columns to jobs table."""


import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Add claim pipeline timestamp columns to jobs table."""
    cursor = conn.execute("PRAGMA table_info(jobs)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if "ev_ordered_at" not in existing_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN ev_ordered_at TIMESTAMP")
    if "adjuster_meeting_at" not in existing_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN adjuster_meeting_at TIMESTAMP")
    if "claim_filed_at" not in existing_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN claim_filed_at TIMESTAMP")
