"""Migration 0027: Add revoked_tokens table for JWT blacklist.

Tracks revoked JWT identifiers (jti) and timestamps to support immediate
session invalidation without waiting for token expiration.
Does NOT modify or touch rep PINs, PIN formats, or PIN verification logic.
"""

from __future__ import annotations

import sqlite3
import structlog

logger = structlog.get_logger("app.core.migrations.0027_add_revoked_tokens")


def up(conn: sqlite3.Connection) -> None:
    """Create revoked_tokens table."""
    logger.info("applying_migration", version=27, name="add_revoked_tokens")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti TEXT PRIMARY KEY,
            revoked_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revoked_tokens_jti ON revoked_tokens(jti)")
    logger.info("migration_complete", version=27)
