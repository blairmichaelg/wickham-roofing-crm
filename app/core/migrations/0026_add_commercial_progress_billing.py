"""Migration 0026: Add commercial progress billing and retainage tables.

Supports multi-stage progress billing schedules of values (SOV), retainage tracking,
and configurable contract terms for commercial roofing jobs.
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0026_add_commercial_progress_billing")


def up(conn: sqlite3.Connection) -> None:
    """Create progress billing schedule, application, and item tracking tables."""
    logger.info("applying_migration", version=26, name="commercial_progress_billing")

    # 1. Schedule of Values (SOV) line items per job
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_billing_schedules (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            item_code TEXT NOT NULL,
            description TEXT NOT NULL,
            scheduled_value_cents INTEGER NOT NULL CHECK(scheduled_value_cents >= 0),
            order_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pb_schedules_job_id ON progress_billing_schedules(job_id)")

    # 2. Progress Billing Applications (Invoicing cycles per commercial contract)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_billing_applications (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            application_no INTEGER NOT NULL CHECK(application_no > 0),
            billing_date TEXT NOT NULL,
            period_start TEXT,
            period_end TEXT,
            retainage_percent REAL NOT NULL CHECK(retainage_percent >= 0.0 AND retainage_percent <= 100.0),
            total_completed_cents INTEGER NOT NULL DEFAULT 0,
            stored_materials_cents INTEGER NOT NULL DEFAULT 0,
            total_billed_cents INTEGER NOT NULL DEFAULT 0,
            retainage_withheld_cents INTEGER NOT NULL DEFAULT 0,
            retainage_released_cents INTEGER NOT NULL DEFAULT 0,
            net_payment_due_cents INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'SUBMITTED', 'PAID', 'VOID')),
            reconciled_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_id, application_no)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pb_apps_job_id ON progress_billing_applications(job_id)")

    # 3. Progress Billing Line Items (per application, tracking SOV line progress)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progress_billing_items (
            id TEXT PRIMARY KEY,
            application_id TEXT NOT NULL REFERENCES progress_billing_applications(id) ON DELETE CASCADE,
            schedule_item_id TEXT NOT NULL REFERENCES progress_billing_schedules(id),
            work_completed_cents INTEGER NOT NULL DEFAULT 0 CHECK(work_completed_cents >= 0),
            stored_materials_cents INTEGER NOT NULL DEFAULT 0 CHECK(stored_materials_cents >= 0),
            total_earned_cents INTEGER NOT NULL DEFAULT 0 CHECK(total_earned_cents >= 0),
            percentage_complete REAL NOT NULL DEFAULT 0.0 CHECK(percentage_complete >= 0.0 AND percentage_complete <= 100.0),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pb_items_app_id ON progress_billing_items(application_id)")

    # 4. Add last_work_date column to jobs for commercial lien / completion tracking
    cursor = conn.execute("PRAGMA table_info(jobs)")
    job_cols = {row[1] for row in cursor.fetchall()}
    if "last_work_date" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_work_date TEXT")
        logger.info("added_column_to_jobs", column="last_work_date")

    logger.info("migration_complete", version=26)
