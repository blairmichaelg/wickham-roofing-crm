import asyncio
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import JobStatus, atomic_qbo_export, get_connection
from app.main import app
from app.workers.supplement_processor import process_supplement_event

client = TestClient(app)
response = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = response.cookies.get("auth_token")
client.cookies.set("auth_token", auth_cookie)

@pytest.fixture(autouse=True)
def mock_pdf_detector():
    with patch("app.api.office_routes.detect_pdf_format", return_value="EAGLEVIEW"), \
         patch("app.core.pipeline.detect_pdf_format", return_value="EAGLEVIEW"):
        yield

@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()

def setup_test_job(conn: sqlite3.Connection, status: str = "SUPPLEMENT_GENERATED") -> str:
    job_id = str(uuid.uuid4())
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status)
        VALUES (?, 'Test User', '123 Test St', 'Testville', 'TS', '12345', '555-5555', ?)
        """,
        (job_id, status)
    )
    conn.execute("COMMIT")
    return job_id

def setup_test_financials(conn: sqlite3.Connection, job_id: str, carrier_rcv_cents: float = 0.0, qbo_exported: int = 0):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, permits_fee_cents, qbo_exported)
        VALUES (?, 100000, ?, 10000, 10000, 10, 0, 0, ?)
        """,
        (job_id, carrier_rcv_cents, qbo_exported)
    )
    conn.execute("COMMIT")

@pytest.mark.asyncio
@patch('app.core.pipeline.PDFGenerator.generate_supplement_pdf')
@patch('app.core.pipeline.extract_eagleview_data')
@patch('app.services.document_parser.parse_statement_of_loss')
@patch('app.core.pipeline.reconcile')
@patch('app.core.pipeline.parse_code_files')
@patch('app.core.pipeline.get_relevant_codes')
@patch('app.services.ai_service.GeminiClient.generate_supplement_narrative')
@patch('app.core.pipeline.generate_and_gate_flags')
async def test_supplement_pdf_not_deleted_after_vault(
    mock_gate, mock_narrative, mock_get_codes, mock_parse_codes, mock_reconcile, mock_parse_sol, mock_ev, mock_gen_pdf, db_conn, tmp_path
):
    job_id = setup_test_job(db_conn, "EV_PARSED")
    
    mock_gen_pdf.return_value = str(tmp_path / "temp_mock.pdf")
    # Actually create the temp mock file
    with open(str(tmp_path / "temp_mock.pdf"), "w") as f:
        f.write("mock pdf content")
        
    from app.core.supplement_models import EagleViewData
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=20.0, ridge_lf=0, hip_lf=0,
        eaves_lf=50.0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=2, predominant_pitch="6/12"
    )
    mock_ev.return_value = (ev_data, "hash")
    mock_parse_sol.return_value = MagicMock(line_items=[])
    
    # Also mock financials so it doesn't fail Fix 2
    mock_sol = MagicMock(line_items=[])
    mock_sol.financials.gross_rcv.verified = True
    mock_parse_sol.return_value = mock_sol
    mock_reconcile.return_value = MagicMock(model_dump_json=lambda: "{}")
    mock_parse_codes.return_value = None
    mock_get_codes.return_value = ""
    mock_narrative.return_value = "narrative"
    mock_gate.return_value = False

    result = await process_supplement_event(
        ctx={"role": "admin"}, job_id=job_id, ev_pdf_path="dummy", sol_pdf_path="dummy", ev_sha256="dummy", ev_doc_id="dummy", sol_sha256="dummy", sol_doc_id="dummy", role="admin"
    )

    assert result["status"] == "success"
    
    # Check that temp file no longer exists
    assert not (tmp_path / "temp_mock.pdf").exists()
    
    # Check that permanent file exists
    vault_path = Path("data/field_docs") / job_id / "Supplement_Request.pdf"
    assert vault_path.exists()
    
    # Assert result contains the correct permanent pdf path
    assert result["pdf_path"] == str(vault_path)
    
    # Clean up
    vault_path.unlink()

def test_accounting_brief_rcv_is_live_not_mock(db_conn):
    db_conn.execute("DELETE FROM financials")
    db_conn.commit()
    job_id = setup_test_job(db_conn, "SUPPLEMENT_GENERATED")
    setup_test_financials(db_conn, job_id, carrier_rcv_cents=500000)

    response = client.get("/api/office/accounting/brief")
    assert response.status_code == 200
    data = response.json()
    assert data["supplemented_rcv_added"] == "$5,000.00"

@pytest.mark.asyncio
async def test_atomic_qbo_export_prevents_double_export(db_conn):
    job1 = setup_test_job(db_conn, "INVOICED")
    job2 = setup_test_job(db_conn, "SUPPLEMENT_APPROVED")
    setup_test_financials(db_conn, job1)
    setup_test_financials(db_conn, job2)

    async def call_atomic():
        return await asyncio.to_thread(atomic_qbo_export)

    # Run two concurrently
    results = await asyncio.gather(call_atomic(), call_atomic())
    
    # Only one of the calls should have successfully returned the 2 rows. 
    # The other call should return 0 rows.
    batch1, batch2 = results
    
    total_returned = len(batch1) + len(batch2)
    assert total_returned == 2
    
    cursor = db_conn.execute("SELECT qbo_exported FROM financials WHERE job_id IN (?, ?)", (job1, job2))
    rows = cursor.fetchall()
    for row in rows:
        assert row["qbo_exported"] == 1

def test_download_export_path_traversal_blocked():
    response = client.get("/api/office/download/.env")
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_resume_fails_gracefully_without_saved_report(db_conn):
    job_id = setup_test_job(db_conn, "PENDING_OPERATOR_REVIEW")
    
    result = await process_supplement_event(
        ctx={"role": "admin"}, job_id=job_id, ev_pdf_path="dummy", sol_pdf_path="dummy", ev_sha256="dummy", ev_doc_id="dummy", sol_sha256="dummy", sol_doc_id="dummy", resume=True, role="admin"
    )
    
    assert result == {"status": "failed", "reason": "no_saved_report"}
    
    cursor = db_conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    status = cursor.fetchone()["status"]
    assert status == JobStatus.PIPELINE_FAILED.value


@patch('app.core.pipeline.extract_eagleview_data')
@patch('app.services.ai_service.GeminiClient.generate_supplement_narrative')
@patch('app.core.pipeline.get_relevant_codes')
@patch('app.core.pipeline.parse_code_files')
@pytest.mark.asyncio
async def test_resume_succeeds_with_saved_report(
    mock_parse_codes, mock_get_codes, mock_narrative, mock_ev, db_conn, tmp_path
):
    from app.core.supplement_models import DiscrepancyReport, MaterialBOM
    
    job_id = setup_test_job(db_conn, "PENDING_OPERATOR_REVIEW")
    
    # Ensure no manual review flags exist
    db_conn.execute("DELETE FROM supplement_flags WHERE job_id = ?", (job_id,))
    
    # Create a saved report
    bom = MaterialBOM(
        field_shingle_bundles=30,
        starter_bundles=3,
        ridge_cap_bundles=3,
        ice_water_rolls=2,
        underlayment_rolls=4,
        drip_edge_pieces=10
    )
    report = DiscrepancyReport(
        job_id=job_id,
        ev_normalized_squares=50.0,
        sol_total_rfg_squares=45.0,
        square_variance=5.0,
        waste_explanation="A 14% waste factor is mathematically required (Complexity Score: 2.80) due to 4 intersecting facets, 50.0 LF of valleys, and a 10/12 pitch.",
        material_bom=bom,
        discrepancies=[]
    )
    
    db_conn.execute('''
        INSERT INTO supplement_reports (job_id, report_json)
        VALUES (?, ?)
    ''', (job_id, report.model_dump_json()))
    db_conn.commit()
    
    mock_get_codes.return_value = ""
    mock_parse_codes.return_value = None
    mock_narrative.return_value = "Resumed narrative"
    
    with patch('app.services.pdf.PDFGenerator.generate_supplement_pdf') as mock_gen_pdf:
        mock_gen_pdf.return_value = str(tmp_path / "resume_mock.pdf")
        with open(str(tmp_path / "resume_mock.pdf"), "w") as f:
            f.write("mock pdf content")
            
        result = await process_supplement_event(
            ctx={"role": "admin"}, job_id=job_id, ev_pdf_path="dummy", sol_pdf_path="dummy",
            ev_sha256="dummy", ev_doc_id="dummy", sol_sha256="dummy", sol_doc_id="dummy", resume=True, role="admin"
        )
    
    # Verify extract_eagleview_data was NOT called
    mock_ev.assert_not_called()
    
    # Verify GeminiClient was called with the restored report
    mock_narrative.assert_called_once()
    called_report = mock_narrative.call_args[0][0]
    assert "A 14% waste factor is mathematically required" in called_report.waste_explanation
    assert "Complexity Score: 2.80" in called_report.waste_explanation
    
    # Verify codes were fetched
    mock_get_codes.assert_called_once()
    
    # Verify pipeline succeeded
    assert result["status"] == "success"
    
    # Verify job status transition
    cursor = db_conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    status = cursor.fetchone()["status"]
    assert status == JobStatus.SUPPLEMENT_GENERATED.value


@patch('app.core.pipeline.extract_eagleview_data')
@patch('app.services.pdf.PDFGenerator.generate_material_po')
@patch('app.core.database.insert_material_order')
@patch('app.config.FIELD_DOCS_DIR')
@pytest.mark.asyncio
async def test_generate_material_order_pipeline_dynamic_waste(
    mock_field_docs, mock_insert_po, mock_gen_po, mock_ev, db_conn, tmp_path
):
    from app.core.supplement_models import EagleViewData
    job_id = setup_test_job(db_conn, "APPROVED")
    setup_test_financials(db_conn, job_id, carrier_rcv_cents=1000000)
    
    mock_field_docs.__truediv__.return_value = tmp_path
    
    # Mock EagleView with high complexity
    _ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=100.0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=5, predominant_pitch="12/12"
    )
    # Score = 5*0.2(1.0) + 1.0(valley) + 2.5(pitch) = 4.5
    # Waste = 0.10 + 0.045 = 0.145 -> 14.5%
    # Normalized squares = 10 * 1.145 = 11.45
    # Field Shingle Bundles (11.45 * 3 = 34.35 -> 35 bundles)
    # With 15% static waste, it would be 10 * 1.15 = 11.5 * 3 = 34.5 -> 35 bundles.
    # Let's make it a bigger difference so 15% vs dynamic waste results in different bundles.
    
    ev_data_high = EagleViewData(
        total_area_sf=10000.0, rake_lf=0, valley_lf=500.0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=20, predominant_pitch="15/12"
    )
    # Score = 20*0.2(4.0) + 5.0(valley) + 4.0(pitch) = 13.0
    # Waste = 0.10 + 0.13 = 0.23 -> Clamped to 0.22 (22%)
    # Normalized squares = 100 * 1.22 = 122.0 SQ
    # Bundles = 122 * 3 = 366
    # If static 15%: 100 * 1.15 = 115 SQ -> 345 bundles.
    
    mock_ev.return_value = (ev_data_high, "hash")
    
    # Create fake ev file so it passes the exists() check
    (tmp_path / "eagleview.pdf").touch()
    
    from app.core.pipeline import generate_material_order_pipeline
    result = await generate_material_order_pipeline(job_id, "ABC Supply", "2026-08-01")
    
    assert result["status"] == "success"
    
    # Check that BOM generated uses dynamic waste (366 bundles) not static (345)
    mock_gen_po.assert_called_once()
    called_bom = mock_gen_po.call_args[0][1]
    assert called_bom.field_shingle_bundles == 366
