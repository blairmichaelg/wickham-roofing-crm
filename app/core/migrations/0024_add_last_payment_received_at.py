"""Migration 0024: Add last_payment_received_at column to jobs and financials tables."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Add last_payment_received_at timestamp to jobs and financials."""
    # Check jobs table
    cursor = conn.execute("PRAGMA table_info(jobs)")
    jobs_cols = {row[1] for row in cursor.fetchall()}
    if "last_payment_received_at" not in jobs_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_payment_received_at TIMESTAMP")

    # Check financials table
    cursor = conn.execute("PRAGMA table_info(financials)")
    fin_cols = {row[1] for row in cursor.fetchall()}
    if "last_payment_received_at" not in fin_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN last_payment_received_at TIMESTAMP")
