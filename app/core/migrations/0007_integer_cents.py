import sqlite3

import structlog

logger = structlog.get_logger("app.core.migrations.0007_integer_cents")

def up(conn: sqlite3.Connection) -> None:
    """Migrate REAL currency columns to INTEGER cents."""
    logger.info("applying_migration", version=7, name="integer_cents")
    
    # 1. Financials Table
    financial_cols = [
        "revenue_cents",
        "carrier_rcv_cents",
        "material_cost_cents",
        "labor_cost_cents",
        "permits_fee_cents",
        "deductible_cents",
        "acv_payment_cents",
        "recoverable_depreciation_cents",
    ]
    financial_cols_nullable = [
        "carrier_initial_rcv_cents",
        "carrier_supplemented_rcv_cents"
    ]
    for col in financial_cols:
        try:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
    for col in financial_cols_nullable:
        try:
            conn.execute(f"ALTER TABLE financials ADD COLUMN {col} INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
            
    # Backfill Financials
    try:
        conn.execute("""
            UPDATE financials 
            SET revenue_cents = CAST(ROUND(revenue * 100) AS INTEGER),
                carrier_rcv_cents = CAST(ROUND(carrier_rcv * 100) AS INTEGER),
                material_cost_cents = CAST(ROUND(material_cost * 100) AS INTEGER),
                labor_cost_cents = CAST(ROUND(labor_cost * 100) AS INTEGER),
                permits_fee_cents = CAST(ROUND(permits_fee * 100) AS INTEGER),
                deductible_cents = CAST(ROUND(deductible * 100) AS INTEGER),
                acv_payment_cents = CAST(ROUND(acv_payment * 100) AS INTEGER),
                recoverable_depreciation_cents = CAST(ROUND(recoverable_depreciation * 100) AS INTEGER),
                carrier_initial_rcv_cents = CAST(ROUND(carrier_initial_rcv * 100) AS INTEGER),
                carrier_supplemented_rcv_cents = CAST(ROUND(carrier_supplemented_rcv * 100) AS INTEGER)
        """)
    except sqlite3.OperationalError:
        pass

    # 2. Jobs Table Check Amounts
    job_cols = [
        "acv_check_amount_cents",
        "supplement_check_amount_cents"
    ]
    for col in job_cols:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} INTEGER DEFAULT NULL")
        except sqlite3.OperationalError:
            pass

    # Backfill Jobs Checks
    try:
        conn.execute("""
            UPDATE jobs
            SET acv_check_amount_cents = CAST(ROUND(acv_check_amount * 100) AS INTEGER),
                supplement_check_amount_cents = CAST(ROUND(supplement_check_amount * 100) AS INTEGER)
            WHERE acv_check_amount IS NOT NULL OR supplement_check_amount IS NOT NULL
        """)
    except sqlite3.OperationalError:
        pass
    
    # 3. Pricing Table
    try:
        conn.execute("ALTER TABLE pricing ADD COLUMN default_rate_cents INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    conn.execute("""
        UPDATE pricing
        SET default_rate_cents = CAST(ROUND(default_rate * 100) AS INTEGER)
    """)
    
    # 4. Recreate Views that depend on financial columns
    conn.execute("DROP VIEW IF EXISTS financial_delta_view")
    conn.execute('''
        CREATE VIEW financial_delta_view AS
        SELECT 
            j.id as job_id,
            j.homeowner_name,
            f.carrier_initial_rcv_cents / 100.0 as carrier_initial_rcv,
            f.carrier_supplemented_rcv_cents / 100.0 as carrier_supplemented_rcv,
            f.revenue_cents / 100.0 as revenue,
            (f.carrier_supplemented_rcv_cents - f.carrier_initial_rcv_cents) / 100.0 AS carrier_rcv_delta,
            (f.revenue_cents - f.carrier_supplemented_rcv_cents) / 100.0 AS contractor_over_carrier
        FROM jobs j
        JOIN financials f ON j.id = f.job_id
    ''')
    
    # Intentionally NOT dropping the old REAL columns in this migration to keep it fully reversible.

