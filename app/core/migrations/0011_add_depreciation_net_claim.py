"""
Migration 0011: Add depreciation_cents and net_claim_cents to financials table.

Adds two missing SoL-sourced financial fields:
- depreciation_cents: Total depreciation held back by the carrier (INTEGER cents)
- net_claim_cents: Initial net payout to homeowner (ACV - deductible, INTEGER cents)

These are written back from parse_statement_of_loss() after a successful SoL parse.
Safe to run on existing DBs — uses ALTER TABLE with try/except per project convention.
"""
import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0011_add_depreciation_net_claim")

def up(conn: sqlite3.Connection) -> None:
    logger.info("applying_migration", version=11, name="add_depreciation_net_claim")
    for col in [
        "depreciation_cents INTEGER DEFAULT 0",
        "net_claim_cents INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {col}")
            logger.info("column_added", col=col)
        except sqlite3.OperationalError:
            logger.info("column_already_exists", col=col)
