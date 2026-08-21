import sqlite3


def up(conn: sqlite3.Connection) -> None:
    """Drop the legacy REAL columns from the financials table."""
    conn.execute("DROP VIEW IF EXISTS financial_delta_view;")
    for col in ["revenue", "carrier_rcv", "material_cost", "labor_cost", "permits_fee"]:
        try:
            conn.execute(f"ALTER TABLE financials DROP COLUMN {col};")
        except sqlite3.OperationalError:
            pass
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
