"""
Migration 0021: Add soft-delete support (deleted_at) to jobs, job_documents, and job_agreements.
Implements 7-Year Statutory Document Retention standards (O.C.G.A. § 10-1-393.12).
"""

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0021_add_soft_delete_and_retention")


def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=21, name="add_soft_delete_and_retention")

    # --- jobs: add deleted_at ---
    cursor = conn.execute("PRAGMA table_info(jobs)")
    job_cols = {row[1] for row in cursor.fetchall()}
    if "deleted_at" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL")
        logger.info("column_added", table="jobs", col="deleted_at")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_deleted_at ON jobs(deleted_at)")

    # --- job_documents: add deleted_at ---
    cursor = conn.execute("PRAGMA table_info(job_documents)")
    doc_cols = {row[1] for row in cursor.fetchall()}
    if "deleted_at" not in doc_cols:
        conn.execute("ALTER TABLE job_documents ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL")
        logger.info("column_added", table="job_documents", col="deleted_at")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_documents_deleted_at ON job_documents(job_id, deleted_at)")

    # --- job_agreements: add deleted_at ---
    cursor = conn.execute("PRAGMA table_info(job_agreements)")
    agreement_cols = {row[1] for row in cursor.fetchall()}
    if "deleted_at" not in agreement_cols:
        conn.execute("ALTER TABLE job_agreements ADD COLUMN deleted_at TIMESTAMP DEFAULT NULL")
        logger.info("column_added", table="job_agreements", col="deleted_at")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_job_agreements_deleted_at ON job_agreements(job_id, deleted_at)")

    logger.info("migration_complete", version=21)
