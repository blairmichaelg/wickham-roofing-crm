"""Tests for Task 5: SQLite WAL concurrency tuning and checkpoint operations."""

import sqlite3
import pytest

from app.core.database import get_connection, wal_checkpoint_truncate


def test_sqlite_wal_busy_timeout_configured():
    """Verify that every connection opened via get_connection has busy_timeout set to 30000ms."""
    conn = get_connection()
    try:
        cursor = conn.execute("PRAGMA busy_timeout;")
        timeout_ms = cursor.fetchone()[0]
        assert timeout_ms == 30000, f"Expected busy_timeout of 30000ms, got {timeout_ms}ms"

        # Verify synchronous mode is NORMAL for WAL concurrency
        cursor = conn.execute("PRAGMA synchronous;")
        sync_mode = cursor.fetchone()[0]
        # NORMAL is 1 in SQLite
        assert sync_mode in (1, "NORMAL")
    finally:
        conn.close()


def test_wal_checkpoint_truncate():
    """Verify that wal_checkpoint_truncate executes PRAGMA wal_checkpoint(TRUNCATE) safely."""
    result = wal_checkpoint_truncate()
    assert isinstance(result, dict)
    assert "busy" in result
    assert "log" in result
    assert "checkpointed" in result
    # On a valid database connection, busy is 0 (not blocked)
    assert result["busy"] == 0
