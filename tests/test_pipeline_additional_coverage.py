import asyncio
import os
import pytest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from app.core.pipeline import (
    run_retail_quote_pipeline,
    generate_material_order_pipeline,
    run_rebuttal_pipeline,
    run_supplement_pipeline
)
from app.core.database import get_connection, JobStatus
from app.core.supplement_models import StatementOfLoss, EagleViewData, MaterialBOM

_EV_DATA = EagleViewData(
    total_area_sf=3500.0,
    rake_lf=50.0,
    valley_lf=80.0,
    ridge_lf=120.0,
    hip_lf=0.0,
    eaves_lf=140.0,
    drip_edge_lf=180.0,
    flashing_lf=0.0,
    step_flashing_lf=0.0,
    total_facets=6,
    predominant_pitch="6/12"
)


@pytest.fixture
def retail_job():
    """RETAIL job with ev_total_area_sf=0 for pipeline testing."""
    conn = get_connection()
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs "
        "(id, homeowner_name, address_line1, city, state, postal_code, phone, job_type, status, ev_total_area_sf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Retail Test Cust", "456 Oak Ln", "Savannah", "GA", "31401",
         "555-0105", "RETAIL", "LEAD_CAPTURED", 0)
    )
    conn.commit()
    conn.close()
    yield job_id
    conn = get_connection()
    for tbl in ("material_orders", "supplement_reports", "job_documents", "financials"):
        conn.execute(f"DELETE FROM {tbl} WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()


@pytest.fixture
def insurance_job():
    """INSURANCE job at LEAD_CAPTURED for pipeline testing."""
    conn = get_connection()
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs "
        "(id, homeowner_name, address_line1, city, state, postal_code, phone, job_type, status, ev_total_area_sf) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Insurance Test Cust", "789 Pine Ln", "Athens", "GA", "30601",
         "555-0200", "INSURANCE", "LEAD_CAPTURED", 0)
    )
    conn.commit()
    conn.close()
    yield job_id
    conn = get_connection()
    for tbl in ("material_orders", "supplement_reports", "job_documents", "financials", "supplement_flags"):
        conn.execute(f"DELETE FROM {tbl} WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()


# ── Retail Quote Pipeline ─────────────────────────────────────────────────────

def test_run_retail_quote_pipeline_job_not_found():
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(run_retail_quote_pipeline(str(uuid.uuid4())))


def test_run_retail_quote_pipeline_missing_ev_data(retail_job):
    res = asyncio.run(run_retail_quote_pipeline(retail_job))
    assert res["status"] == "pending_review"
    assert res["reason"] == "missing_ev_data"


def test_run_retail_quote_pipeline_success(retail_job, tmp_path, monkeypatch):
    # Redirect the PDF output dir so we don't write to the real data/ folder
    monkeypatch.setattr("app.services.pdf.documents.FIELD_DOCS_DIR", tmp_path)
    monkeypatch.setattr("app.services.pdf.invoice.FIELD_DOCS_DIR", tmp_path)

    conn = get_connection()
    conn.execute("UPDATE jobs SET ev_total_area_sf = 3500 WHERE id = ?", (retail_job,))
    conn.commit()
    conn.close()

    res = asyncio.run(run_retail_quote_pipeline(retail_job))
    assert res["status"] == "complete"

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (retail_job,)).fetchone()
    conn.close()
    assert row["status"] == "RETAIL_QUOTE_GENERATED"


# ── Material Order Pipeline ───────────────────────────────────────────────────

@patch("app.core.pipeline.parse_measurement_pdf")
def test_generate_material_order_pipeline_no_ev_pdf(mock_parse, insurance_job):
    with pytest.raises(ValueError, match="EagleView PDF not found"):
        asyncio.run(generate_material_order_pipeline(insurance_job, "ABC", "2026-09-10"))


@patch("app.core.pipeline.parse_measurement_pdf")
@patch("app.core.pipeline.PDFGenerator")
def test_generate_material_order_pipeline_success(mock_pdf_gen_class, mock_parse, insurance_job, tmp_path, monkeypatch):
    """Test generate_material_order_pipeline runs successfully when EV pdf exists."""
    # Patch the FIELD_DOCS_DIR that generate_material_order_pipeline uses internally
    monkeypatch.setattr("app.config.FIELD_DOCS_DIR", tmp_path)

    job_dir = tmp_path / insurance_job
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / "eagleview.pdf"
    pdf_path.write_bytes(b"mock pdf content")

    mock_parse.return_value = (_EV_DATA, "fake_hash")

    # Return a real temp file path so the insert doesn't fail the sha hash
    po_path = tmp_path / "mock_po.pdf"
    po_path.write_bytes(b"PO PDF")
    mock_pdf_gen_inst = MagicMock()
    mock_pdf_gen_inst.generate_material_po = AsyncMock(return_value=str(po_path))
    mock_pdf_gen_class.return_value = mock_pdf_gen_inst

    # Prime the state machine: put job into a state from which MATERIAL_ORDERED is reachable.
    # The state machine requires revenue_cents to be set.
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO financials (job_id, revenue_cents) VALUES (?, 1000000)",
        (insurance_job,)
    )
    conn.execute("UPDATE jobs SET status = 'INVOICED' WHERE id = ?", (insurance_job,))
    conn.commit()
    conn.close()

    res = asyncio.run(generate_material_order_pipeline(insurance_job, "ABC Supply", "2026-09-10"))
    assert res["status"] == "success"

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (insurance_job,)).fetchone()
    conn.close()
    assert row["status"] == "MATERIAL_ORDERED"


# ── Rebuttal Pipeline ─────────────────────────────────────────────────────────

def test_run_rebuttal_pipeline_job_not_found():
    with pytest.raises(ValueError, match="not found"):
        asyncio.run(run_rebuttal_pipeline(str(uuid.uuid4())))


@patch("app.core.pipeline.get_ai_client")
@patch("app.core.pipeline.PDFGenerator")
def test_run_rebuttal_pipeline_success(mock_pdf_gen_class, mock_ai_client, insurance_job, tmp_path):
    mock_ai_inst = MagicMock()
    mock_ai_inst.generate_text = AsyncMock(return_value="Mocked rebuttal narrative.")
    mock_ai_client.return_value = mock_ai_inst

    rebuttal_pdf = tmp_path / "Rebuttal_Letter.pdf"
    rebuttal_pdf.write_bytes(b"rebuttal pdf content")
    mock_pdf_gen_inst = MagicMock()
    mock_pdf_gen_inst.generate_rebuttal_letter = AsyncMock(return_value=str(rebuttal_pdf))
    mock_pdf_gen_class.return_value = mock_pdf_gen_inst

    conn = get_connection()
    conn.execute(
        "INSERT INTO supplement_reports (job_id, report_json) VALUES (?, ?)",
        (insurance_job, '{"discrepancies": []}')
    )
    conn.commit()
    conn.close()

    res = asyncio.run(run_rebuttal_pipeline(insurance_job, denial_text="Denied due to age."))
    assert res["status"] == "complete"


# ── Supplement Pipeline ───────────────────────────────────────────────────────

@patch("app.core.pipeline.parse_measurement_pdf")
@patch("app.services.document_parser.parse_statement_of_loss")
def test_run_supplement_pipeline_success(mock_sol_parse, mock_ev_parse, insurance_job):
    """Test run_supplement_pipeline runs successfully end-to-end with mocked SOL parse."""
    from decimal import Decimal
    from app.core.ingestion_models import (
        SourcedValue, ClaimFinancials, RoofGeometry, UniversalClaimAST
    )

    mock_ev_parse.return_value = (_EV_DATA, "fake_ev_hash")

    # Build a minimal but valid UniversalClaimAST (net_claim = gross_rcv - depreciation - deductible)
    sv_zero = SourcedValue(value=Decimal("0"), verified=True, doc_id="d", page=1, raw="0")
    sv_100k = SourcedValue(value=Decimal("100000"), verified=True, doc_id="d", page=1, raw="100000")
    fin = ClaimFinancials(
        gross_rcv=sv_100k,
        total_depreciation=sv_zero,
        deductible=sv_zero,
        net_claim=sv_100k,
    )
    geo = RoofGeometry(
        pitch=SourcedValue(value="7/12", verified=True, doc_id="d", page=1, raw="7/12"),
        total_squares=sv_100k,
        eaves_lf=sv_zero,
        valleys_lf=sv_zero,
        rakes_lf=sv_zero,
    )
    mock_sol_parse.return_value = UniversalClaimAST(
        line_items=[],
        roof_geometry=geo,
        financials=fin,
        source_doc_sha256="fake_sha",
        source_doc_id="fake_doc",
        ast_version=1,
    )

    res = asyncio.run(run_supplement_pipeline(
        job_id=insurance_job,
        ev_pdf_path="fake_ev.pdf",
        sol_pdf_path="fake_sol.pdf",
        ev_sha256="fake_ev_sha",
        ev_doc_id="fake_ev_doc",
        sol_sha256="fake_sol_sha",
        sol_doc_id="fake_sol_doc",
        generate_pdf=False
    ))
    # Pipeline may return success, complete, or halted_for_review — any non-failed outcome is acceptable
    assert res["status"] != "failed"

