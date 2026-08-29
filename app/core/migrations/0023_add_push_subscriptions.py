"""
Migration 0023: Add push_subscriptions table for VAPID Web Push notifications.
Stores Web Push endpoint, encryption keys (p256dh, auth), user_id/role, and timestamps.
"""

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0023_add_push_subscriptions")


def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=23, name="add_push_subscriptions")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            role TEXT DEFAULT 'field',
            endpoint TEXT UNIQUE NOT NULL,
            p256dh_key TEXT NOT NULL,
            auth_key TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'utc')),
            last_used_at TEXT DEFAULT (datetime('now', 'utc'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subs_user ON push_subscriptions (user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subs_role ON push_subscriptions (role)"
    )

    logger.info("migration_complete", version=23)
