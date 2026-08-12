"""Migration 0016: Add payment tracking flags to financials table and support new payment status columns."""


def up(conn):
    """Add ACV, depreciation, retail payment timestamps and deductible paid flag to financials."""
    cursor = conn.execute("PRAGMA table_info(financials)")
    existing_cols = {row[1] for row in cursor.fetchall()}

    if "acv_payment_received_at" not in existing_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN acv_payment_received_at TIMESTAMP")
    if "depreciation_payment_received_at" not in existing_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN depreciation_payment_received_at TIMESTAMP")
    if "retail_payment_received_at" not in existing_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN retail_payment_received_at TIMESTAMP")
    if "deductible_paid" not in existing_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN deductible_paid INTEGER DEFAULT 0")
    if "deductible_paid_cents" not in existing_cols:
        conn.execute("ALTER TABLE financials ADD COLUMN deductible_paid_cents INTEGER DEFAULT 0")
