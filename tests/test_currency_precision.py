"""Tests for Task 2: Integer cents currency storage, Pydantic coercion, and precision guarantees."""

import pytest
from pydantic import ValidationError

from app.api.office_routes import FinancialsPayload
from app.core.database import get_connection, run_migrations
from app.core.supplement_models import InvoiceLine
from app.services.qbo_export import export_to_csv, InvoiceExport


def test_migration_0025_pricing_integer_cents():
    """Verify that migration 25 enforces integer cents in pricing table."""
    run_migrations()
    conn = get_connection()
    try:
        cursor = conn.execute("PRAGMA table_info(pricing)")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        assert "default_rate_cents" in cols
        assert cols["default_rate_cents"].upper() == "INTEGER"
        # Confirm default_rate REAL column was dropped
        assert "default_rate" not in cols

        # Check seeded values are accurate integer cents
        row = conn.execute("SELECT default_rate_cents FROM pricing WHERE item_key = 'field_shingle_bundles'").fetchone()
        assert row is not None
        assert row["default_rate_cents"] == 10500  # $105.00
    finally:
        conn.close()


def test_financials_payload_coercion_and_bounds():
    """Verify FinancialsPayload accepts various inputs and computes exact integer cents."""
    # Test valid floats and strings with currency symbols
    payload = FinancialsPayload(
        revenue="$12,500.55",
        carrier_rcv="12500.55",
        materials=4500.25,
        labor=3000.00,
        deductible="$1,000.00",
        acv_payment="5000.25",
        recoverable_depreciation="6500.30",
        permits_fee="$150.00",
    )

    assert payload.revenue == 12500.55
    assert payload.revenue_cents == 1250055
    assert payload.materials_cents == 450025
    assert payload.labor_cents == 300000
    assert payload.deductible_cents == 100000
    assert payload.acv_payment_cents == 500025
    assert payload.recoverable_depreciation_cents == 650030
    assert payload.permits_fee_cents == 15000


def test_financials_payload_boundary_values():
    """Test boundary values: $0.01 and large commercial amounts ($10,000,000.00)."""
    payload = FinancialsPayload(
        revenue="10000000.00",
        carrier_rcv="0.01",
        materials="0.00",
        labor="9999999.99",
    )
    assert payload.carrier_rcv_cents == 1
    assert payload.revenue_cents == 1000000000
    assert payload.labor_cents == 999999999


def test_financials_payload_negative_rejected():
    """Negative values should be rejected by the validator."""
    with pytest.raises(ValidationError):
        FinancialsPayload(
            revenue="-100.00",
            carrier_rcv=100.00,
            materials=50.0,
            labor=50.0,
        )


def test_invoice_line_coercion_and_cents():
    """Verify InvoiceLine parses rates/amounts and provides integer cents."""
    line = InvoiceLine(
        item="shingle_install",
        description="Field Shingles",
        quantity=10.0,
        rate="$105.00",
        amount="$1,050.00",
    )
    assert line.rate == 105.00
    assert line.rate_cents == 10500
    assert line.amount == 1050.00
    assert line.amount_cents == 105000


def test_qbo_export_formatting(tmp_path, monkeypatch):
    """Confirm export_to_csv produces exact 2-decimal formatted CSV rows."""
    from pathlib import Path
    monkeypatch.setattr("app.services.qbo_export.EXPORT_DIR", tmp_path)

    lines = [
        InvoiceLine(
            item="shingle_install",
            description="Field Shingle Bundles",
            quantity=5.0,
            rate=105.00,
            amount=525.00,
        ),
        InvoiceLine(
            item="step_flashing_tins",
            description="Step Flashing Tins",
            quantity=20.0,
            rate=0.50,
            amount=10.00,
        ),
    ]
    export = InvoiceExport(
        invoice_no="INV-TEST-001",
        customer="Acme Commercial",
        invoice_date="2026-09-12",
        due_date="2026-10-12",
        lines=lines,
    )
    csv_file = export_to_csv(export)
    content = Path(csv_file).read_text(encoding="utf-8")
    assert "INV-TEST-001,Acme Commercial,2026-09-12,2026-10-12,Roofing:Shingle Installation,Field Shingle Bundles,5.00,105.00,525.00" in content
    assert "0.50,10.00" in content
