"""
Unit and integration tests for Commercial-Grade Platypus PDF generation.

Verifies:
- Multi-page commercial contract generation with Schedule of Values (SOV).
- NumberedCanvas two-pass dynamic page numbering ('Page X of Y').
- KeepInFrame behavior with dynamically long AI/user-generated scope descriptions.
- AIA G702/G703 style progress billing payment application PDF generation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pdfplumber
import pytest

from app.services.pdf.commercial import CommercialPDFGenerator


@pytest.fixture
def commercial_pdf_gen():
    return CommercialPDFGenerator()


@pytest.fixture
def sample_commercial_job():
    return {
        "id": "JOB-COMM-TEST-001",
        "customer_name": "Apex Commercial Logistics LLC",
        "property_address": "1250 Industrial Parkway, Duluth, GA 30096",
        "rep_name": "Marcus Vance",
        "rep_phone": "404-555-0199",
        "retainage_percent": 10.0,
        "contract_amount": 12500000,  # $125,000.00
    }


@pytest.fixture
def sample_sov_items():
    return [
        {
            "item_number": 1,
            "description": "Mobilization, Safety Rigging & Scaffolding",
            "scheduled_value": 1500000,  # $15,000.00
            "prev_billed": 1500000,
            "this_period_billed": 0,
            "total_billed": 1500000,
            "pct_complete": 100.0,
            "balance_to_finish": 0,
            "retainage_held": 150000,  # $1,500.00
        },
        {
            "item_number": 2,
            "description": "TPO Membrane Tear-off & Structural Deck Inspection",
            "scheduled_value": 4500000,  # $45,000.00
            "prev_billed": 2000000,
            "this_period_billed": 2000000,
            "total_billed": 4000000,
            "pct_complete": 88.89,
            "balance_to_finish": 500000,
            "retainage_held": 400000,
        },
        {
            "item_number": 3,
            "description": "Polyiso Insulation & 60-mil Mechanically Attached TPO",
            "scheduled_value": 5000000,  # $50,000.00
            "prev_billed": 0,
            "this_period_billed": 3000000,
            "total_billed": 3000000,
            "pct_complete": 60.0,
            "balance_to_finish": 2000000,
            "retainage_held": 300000,
        },
        {
            "item_number": 4,
            "description": "Coping Cap, Flashing & Edge Metal Fabrication",
            "scheduled_value": 1500000,  # $15,000.00
            "prev_billed": 0,
            "this_period_billed": 0,
            "total_billed": 0,
            "pct_complete": 0.0,
            "balance_to_finish": 1500000,
            "retainage_held": 0,
        },
    ]


def test_cents_to_dollar_str(commercial_pdf_gen):
    assert commercial_pdf_gen._cents_to_dollar_str(12500000) == "$125,000.00"
    assert commercial_pdf_gen._cents_to_dollar_str(150000) == "$1,500.00"
    assert commercial_pdf_gen._cents_to_dollar_str(0) == "$0.00"
    assert commercial_pdf_gen._cents_to_dollar_str(None) == "$0.00"


def test_generate_commercial_contract_pages_and_numbering(
    commercial_pdf_gen, sample_commercial_job, sample_sov_items, tmp_path
):
    output_pdf = str(tmp_path / "commercial_contract.pdf")
    scope = (
        "Full commercial tear-off of 25,000 sq ft warehouse roof down to metal deck. "
        "Installation of 2.6 inch Polyisocyanurate insulation boards (R-15) adhered with low-rise foam adhesive. "
        "Installation of 60-mil Johns Manville TPO single-ply roofing membrane with heat-welded seams. "
        "Furnish and install new 24-gauge pre-finished Kynar 500 coping cap and perimeter edge metal."
    )

    result_path = asyncio.run(
        commercial_pdf_gen.generate_commercial_contract(
            job=sample_commercial_job,
            sov_items=sample_sov_items,
            terms={"retainage_percent": 10.0, "warranty_years": 20},
            scope_text=scope,
            filepath=output_pdf,
        )
    )

    assert Path(result_path).exists()
    assert Path(result_path).stat().st_size > 5000

    # Inspect with pdfplumber to verify multi-page Platypus flow and NumberedCanvas
    with pdfplumber.open(result_path) as pdf:
        num_pages = len(pdf.pages)
        assert num_pages >= 2, f"Expected at least 2 pages, got {num_pages}"

        # Check footer on each page for "Page X of Y"
        all_text = ""
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text += text
            expected_page_str = f"Page {i} of {num_pages}"
            assert expected_page_str in text, (
                f"Page {i} footer missing '{expected_page_str}'. Page text preview: {text[-200:]}"
            )

        # Verify key content is rendered
        assert "Apex Commercial Logistics LLC" in all_text
        assert "SCHEDULE OF VALUES" in all_text
        assert "Retainage Terms" in all_text
        assert "10.0%" in all_text
        assert "$125,000.00" in all_text


def test_keep_in_frame_prevents_overflow_with_huge_scope(
    commercial_pdf_gen, sample_commercial_job, sample_sov_items, tmp_path
):
    """
    KeepInFrame should safely scale/shrink dynamic text rather than throwing Platypus
    LayoutError / page overflow exceptions when given an extremely large scope description.
    """
    output_pdf = str(tmp_path / "huge_scope_contract.pdf")
    # Massive 6,000 character scope description
    massive_scope = "Comprehensive commercial specification: " + (
        "Phase 1 includes continuous acoustic insulation and deck fastening compliance with ASTM D2178. "
        "All joints staggered 12 inches minimum with hot-air welded laps inspected with blunt seam probe. "
        "Contractor guarantees water-tight integrity for 20 continuous years following substantial completion. "
    ) * 25

    result_path = asyncio.run(
        commercial_pdf_gen.generate_commercial_contract(
            job=sample_commercial_job,
            sov_items=sample_sov_items,
            scope_text=massive_scope,
            filepath=output_pdf,
        )
    )

    assert Path(result_path).exists()
    with pdfplumber.open(result_path) as pdf:
        assert len(pdf.pages) >= 2


def test_generate_progress_billing_invoice(
    commercial_pdf_gen, sample_commercial_job, sample_sov_items, tmp_path
):
    output_pdf = str(tmp_path / "progress_billing_app.pdf")

    billing_app = {
        "app_number": 2,
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "original_contract_sum": 12500000,
        "change_orders_net": 0,
        "contract_sum_to_date": 12500000,
        "total_completed_and_stored": 8500000,
        "retainage_percent": 10.0,
        "total_retainage": 850000,
        "total_earned_less_retainage": 7650000,
        "less_previous_certificates": 1350000,
        "current_payment_due": 6300000,
        "balance_to_finish": 4000000,
    }

    result_path = asyncio.run(
        commercial_pdf_gen.generate_progress_billing_invoice(
            job=sample_commercial_job,
            billing_app=billing_app,
            sov_items=sample_sov_items,
            filepath=output_pdf,
        )
    )

    assert Path(result_path).exists()
    with pdfplumber.open(result_path) as pdf:
        num_pages = len(pdf.pages)
        assert num_pages >= 2, f"Continuation sheet should push to page 2, got {num_pages}"

        p1_text = pdf.pages[0].extract_text() or ""
        assert "APPLICATION AND CERTIFICATE FOR PAYMENT" in p1_text
        assert "App #2" in p1_text or "APPLICATION NO:" in p1_text
        assert "$63,000.00" in p1_text or "63,000.00" in p1_text
        assert f"Page 1 of {num_pages}" in p1_text

        p2_text = pdf.pages[1].extract_text() or ""
        assert "CONTINUATION SHEET" in p2_text
        assert "Schedule of Values" in p2_text or "SCHEDULE OF VALUES" in p2_text
        assert f"Page 2 of {num_pages}" in p2_text
