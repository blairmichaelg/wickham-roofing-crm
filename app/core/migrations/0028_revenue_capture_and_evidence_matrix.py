"""Migration 0028: Add storm opportunities, evidence exhibits, and contact attempts.

Supports:
- Phase A: Storm-to-lead matching, work queue, and internal metrics.
- Phase B: Next Best Action and follow-up attempt logging without paid messaging.
- Phase C: Evidence Matrix v1 exhibits linked to jobs and AST discrepancies.
"""

from __future__ import annotations

import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0028_revenue_capture_and_evidence_matrix")


def up(conn: sqlite3.Connection) -> None:
    """Create storm_opportunities, evidence_exhibits, and contact_attempts tables."""
    logger.info("applying_migration", version=28, name="revenue_capture_and_evidence_matrix")

    # 1. Storm Opportunities Work Queue
    conn.execute("""
        CREATE TABLE IF NOT EXISTS storm_opportunities (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            storm_event_id TEXT REFERENCES storm_events(id) ON DELETE SET NULL,
            matched_date TEXT NOT NULL,
            match_method TEXT NOT NULL,
            severity_summary TEXT NOT NULL,
            targeting_window_hours INTEGER NOT NULL DEFAULT 168,
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'surfaced', 'contacted', 'inspection_scheduled', 'dismissed')),
            assigned_rep_id TEXT REFERENCES field_reps(id) ON DELETE SET NULL,
            contacted_at TIMESTAMP,
            dismissed_reason TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(job_id, storm_event_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storm_opps_job ON storm_opportunities(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storm_opps_status ON storm_opportunities(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_storm_opps_rep ON storm_opportunities(assigned_rep_id)")

    # 2. Evidence Matrix v1 Exhibits
    conn.execute("""
        CREATE TABLE IF NOT EXISTS evidence_exhibits (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            photo_id TEXT,
            photo_path TEXT,
            category TEXT NOT NULL CHECK(category IN ('decking_sheathing', 'flashing_penetration', 'shingle_damage', 'ventilation', 'code_upgrade', 'interior_water_damage', 'debris_access', 'other')),
            observation_text TEXT NOT NULL,
            roof_area_location TEXT,
            exhibit_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'reviewed', 'included', 'excluded')),
            ast_discrepancy_key TEXT,
            requires_office_review INTEGER NOT NULL DEFAULT 0,
            source_provenance TEXT NOT NULL DEFAULT 'FIELD_REP_OBSERVATION',
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_exhibits_job ON evidence_exhibits(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_exhibits_status ON evidence_exhibits(status)")

    # 3. First-Party Contact & Follow-Up Attempts
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contact_attempts (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            opportunity_id TEXT REFERENCES storm_opportunities(id) ON DELETE SET NULL,
            rep_id TEXT,
            rep_name TEXT,
            contact_method TEXT NOT NULL CHECK(contact_method IN ('CALL', 'TEXT', 'DOOR', 'EMAIL')),
            outcome TEXT NOT NULL,
            notes TEXT,
            attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_attempts_job ON contact_attempts(job_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contact_attempts_opp ON contact_attempts(opportunity_id)")

    logger.info("migration_complete", version=28)
