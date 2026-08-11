import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0010_drop_legacy_real_columns")

def up(conn: sqlite3.Connection) -> None:
    """
    Drop remaining legacy REAL dollar-amount columns now that all _cents
    INTEGER columns are confirmed present and backfilled (migration 0007).
    
    Bucket A — financial REAL columns in financials table:
      deductible, acv_payment, recoverable_depreciation,
      carrier_initial_rcv, carrier_supplemented_rcv
      (revenue, carrier_rcv, material_cost, labor_cost, permits_fee
       were already dropped in 0009_drop_real_financials.py)
    
    Bucket A — check amount REAL columns in jobs table:
      acv_check_amount, supplement_check_amount
    
    Bucket B (measurement, geo, storm) and Bucket C (pct) columns are 
    intentionally left as REAL — they are correct as-is.
    
    SQLite requires table rebuild to drop columns for pre-3.35 compat.
    We use ALTER TABLE DROP COLUMN (SQLite >= 3.35, Python 3.11 ships 3.41+).
    """
    logger.info("applying_migration", version=10, name="drop_legacy_real_columns")
    
    # --- financials table ---
    legacy_financial_cols = [
        "deductible",
        "acv_payment", 
        "recoverable_depreciation",
        "carrier_initial_rcv",
        "carrier_supplemented_rcv",
    ]
    for col in legacy_financial_cols:
        try:
            conn.execute(f"ALTER TABLE financials DROP COLUMN {col}")
            logger.info("dropped_column", table="financials", column=col)
        except Exception as e:
            logger.warning("column_drop_skipped", table="financials", column=col, reason=str(e))

    # --- jobs table ---
    legacy_jobs_cols = [
        "acv_check_amount",
        "supplement_check_amount",
    ]
    for col in legacy_jobs_cols:
        try:
            conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
            logger.info("dropped_column", table="jobs", column=col)
        except Exception as e:
            logger.warning("column_drop_skipped", table="jobs", column=col, reason=str(e))

    # Recreate financial_delta_view referencing only _cents columns
    # (0009 already did this, but re-running is idempotent and safe)
    conn.execute("DROP VIEW IF EXISTS financial_delta_view")
    conn.execute('''
        CREATE VIEW financial_delta_view AS
        SELECT 
            j.id as job_id,
            j.homeowner_name,
            f.carrier_initial_rcv_cents,
            f.carrier_supplemented_rcv_cents,
            f.revenue_cents,
            (f.carrier_supplemented_rcv_cents - f.carrier_initial_rcv_cents) AS carrier_rcv_delta_cents,
            (f.revenue_cents - f.carrier_supplemented_rcv_cents) AS contractor_over_carrier_cents
        FROM jobs j
        JOIN financials f ON j.id = f.job_id
    ''')
    logger.info("migration_complete", version=10)
