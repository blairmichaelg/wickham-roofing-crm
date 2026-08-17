"""
Unit tests for the QuickBooks Online (QBO) CSV Export Bridge.
"""

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.supplement_models import InvoiceExport, InvoiceLine, MaterialBOM
from app.services.qbo_export import EXPORT_DIR, QBO_ITEMS, export_to_csv, generate_qbo_invoice


@pytest.fixture(autouse=True)
def clean_exports():
    """Ensure a clean export directory for each test."""
    if EXPORT_DIR.exists():
        for f in EXPORT_DIR.glob("*.csv"):
            f.unlink()
    yield


def test_qbo_export_formats_correctly():
    """Verify correct CSV headers and multi-line flattening."""
    export = InvoiceExport(
        invoice_no="INV-1001",
        customer="John Doe",
        invoice_date="2026-06-30",
        due_date="2026-07-30",
        lines=[
            InvoiceLine(
                item="shingle_install",
                description="Install architectural shingles",
                quantity=30.5,
                rate=100.0,
                amount=3050.0
            ),
            InvoiceLine(
                item="tear_off",
                description="Tear off old roof",
                quantity=30.5,
                rate=50.0,
                amount=1525.0
            )
        ]
    )

    filepath = export_to_csv(export)
    
    assert Path(filepath).exists()
    assert Path(filepath).name == "INV-1001_QBO.csv"

    # Read and verify CSV contents
    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

        # 1 header + 2 data rows
        assert len(rows) == 3
        
        # Verify headers
        assert rows[0] == [
            "InvoiceNo", "Customer", "InvoiceDate", "DueDate", 
            "Item(Product/Service)", "ItemDescription", "ItemQuantity", "ItemRate", "ItemAmount"
        ]

        # Verify Line 1
        assert rows[1][0] == "INV-1001"
        assert rows[1][1] == "John Doe"
        assert rows[1][4] == QBO_ITEMS["shingle_install"]
        assert rows[1][5] == "Install architectural shingles"
        assert rows[1][6] == "30.50"
        assert rows[1][8] == "3050.00"

        # Verify Line 2 (invoice metadata must be repeated)
        assert rows[2][0] == "INV-1001"
        assert rows[2][1] == "John Doe"
        assert rows[2][4] == QBO_ITEMS["tear_off"]


def test_qbo_export_skips_negative_amounts():
    """QBO rejects negative line items; verify they are skipped."""
    export = InvoiceExport(
        invoice_no="INV-1002",
        customer="Jane Smith",
        invoice_date="2026-06-30",
        due_date="2026-07-30",
        lines=[
            InvoiceLine(
                item="shingle_install",
                description="Valid item",
                quantity=1.0,
                rate=100.0,
                amount=100.0
            ),
            InvoiceLine(
                item="discount",
                description="Negative item",
                quantity=1.0,
                rate=-50.0,
                amount=-50.0
            )
        ]
    )

    filepath = export_to_csv(export)

    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

        # 1 header + 1 valid data row (negative skipped)
        assert len(rows) == 2
        assert rows[1][4] == QBO_ITEMS["shingle_install"]


def test_qbo_export_unmapped_item_fallback():
    """Unrecognized item names should pass through unchanged."""
    export = InvoiceExport(
        invoice_no="INV-1003",
        customer="Bob",
        invoice_date="2026-06-30",
        due_date="2026-07-30",
        lines=[
            InvoiceLine(
                item="custom_fee_xyz",
                description="Custom fee",
                quantity=1.0,
                rate=50.0,
                amount=50.0
            )
        ]
    )

    filepath = export_to_csv(export)

    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
        
        # 'custom_fee_xyz' is not in QBO_ITEMS, so it should be output literally
        assert rows[1][4] == "custom_fee_xyz"




@patch("app.core.database.get_financials")
def test_generate_qbo_invoice_includes_op_and_fees(mock_get_financials):
    """Verify that O&P and Permit Fees are injected from the financials table."""
    mock_get_financials.return_value = {
        "overhead_pct": 10.0, 
        "material_cost_cents": 1000000,
        "labor_cost_cents": 500000,
        "permits_fee_cents": 15000
    }
    
    bom = MaterialBOM(
        field_shingle_bundles=30,
        starter_bundles=2,
        ridge_cap_bundles=3,
        ice_water_rolls=1,
        underlayment_rolls=4,
        drip_edge_pieces=10,
        vents_count=0,
        nails_boxes=0,
        sealant_tubes=0
    )
    
    # We mock get_pricing_ledger to keep it isolated
    with patch("app.services.qbo_export.get_pricing_ledger", return_value={"field_shingle_bundles": 100.0}):
        
        filepath = generate_qbo_invoice("demo_job_1", bom)
        
        with open(filepath, encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)
            
            # The last two rows should be O&P and Permit Fees
            op_row = [r for r in rows if r[5] == "Overhead & Profit"][0]
            assert op_row[4] == "General:Overhead and Profit"
            assert op_row[8] == "1500.00"
            
            permit_row = [r for r in rows if r[5] == "Permits & Fees"][0]
            assert permit_row[4] == "General:Permits"
            assert permit_row[8] == "150.00"
