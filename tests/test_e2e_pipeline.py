from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def mock_pdf_detector():
    with patch("app.api.office_routes.detect_pdf_format", return_value="EAGLEVIEW"), \
         patch("app.core.pipeline.detect_pdf_format", return_value="EAGLEVIEW"):
        yield

from app.core.database import get_connection
from app.main import app

client = TestClient(app)
response = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = response.cookies.get("auth_token")
client.cookies.set("auth_token", auth_cookie)

@pytest.fixture(autouse=True)
def setup_teardown():
    # Setup - handled by the app lifespan usually, but we ensure tables exist
    yield
    # Cleanup after tests could be added here

def test_full_job_lifecycle(tmp_path):
    """
    E2E integration test that simulates:
    1. A new lead coming in from the field (POST /api/field/jobs)
    2. Verification of LEAD_CAPTURED state
    3. Triggering the master pipeline (POST /api/office/jobs/{id}/eagleview)
    4. Verifying QBO generation with pricing
    5. Verifying INVOICED final state
    """
    
    # 1. Simulate new lead
    lead_payload = {
        "homeowner_name": "E2E Test User",
        "address_line1": "123 Pipeline Blvd",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "30303",
        "phone": "555-000-1111"
    }
    
    # We bypass ngrok header requirement in testing if we mock it, or just send it
    response = client.post("/api/field/jobs", json=lead_payload, headers={"ngrok-skip-browser-warning": "1"})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    
    # 2. Verify state is LEAD_CAPTURED
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        assert row is not None
        assert row["status"] == "LEAD_CAPTURED"
    finally:
        conn.close()
        
    # Satisfy State Machine pre-requisites
    fin_payload = {
        "revenue": 15000.0,
        "carrier_rcv": 15000.0,
        "materials": 5000.0,
        "labor": 4000.0,
        "overhead_pct": 0.25,
        "commission_pct": 0.10,
        "permits_fee": 150.0
    }
    client.post(f"/api/office/jobs/{job_id}/financials", json=fin_payload)
    
    prod_payload = {
        "supplier_name": "ABC Supply",
        "delivery_date": "2026-07-10T12:00:00Z",
        "crew_name": "Crew Alpha",
        "install_date": "2026-07-12T08:00:00Z"
    }
    client.post(f"/api/office/jobs/{job_id}/production", json=prod_payload)
        
    # 3. Create a fake EV PDF
    fake_pdf = tmp_path / "fake_eagleview.pdf"
    fake_pdf.write_bytes(b"%PDF-dummy pdf content")
    
    # Mock the extract_eagleview_data so we don't need a real PDF
    # We will use dependency injection or patching for the extraction
    from unittest.mock import patch

    from app.core.supplement_models import EagleViewData
    
    mock_ev_data = EagleViewData(
        total_area_sf=3500.0,
        rake_lf=150.0,
        valley_lf=45.0,
        ridge_lf=120.0,
        hip_lf=0.0,
        eaves_lf=200.0,
        drip_edge_lf=350.0,
        flashing_lf=None,
        step_flashing_lf=None,
        total_facets=10,
        predominant_pitch="6/12"
    )
    
    with patch("app.core.pipeline.extract_eagleview_data", return_value=(mock_ev_data, "fake_sha256")):
        with open(fake_pdf, "rb") as f:
            upload_resp = client.post(
                f"/api/office/jobs/{job_id}/eagleview",
                files={"file": ("fake_eagleview.pdf", f, "application/pdf")}
            )
            
    assert upload_resp.status_code == 200
    resp_data = upload_resp.json()
    
    # It should halt for missing flashing
    assert resp_data["pipeline_result"]["status"] == "halted_for_review"
    assert resp_data["pipeline_result"]["reason"] == "missing_flashing_data"
    
    # Submit manual flashing
    flashing_payload = {"flashing_lf": 25.5, "step_flashing_lf": 15.0}
    manual_resp = client.post(f"/api/office/jobs/{job_id}/manual_flashing", json=flashing_payload)
    assert manual_resp.status_code == 200
    
    # Retry the EV upload with different content to avoid duplicate hash check
    fake_pdf_2 = tmp_path / "fake_eagleview_2.pdf"
    fake_pdf_2.write_bytes(b"%PDF-dummy pdf content 2")

    with patch("app.core.pipeline.extract_eagleview_data", return_value=(mock_ev_data, "fake_sha256_2")):
        with open(fake_pdf_2, "rb") as f:
            upload_resp = client.post(
                f"/api/office/jobs/{job_id}/eagleview",
                files={"file": ("fake_eagleview_2.pdf", f, "application/pdf")}
            )
            
    assert upload_resp.status_code == 200
    resp_data = upload_resp.json()
    assert resp_data["pipeline_result"]["status"] == "success"
    
    # 4. Verify QBO generated with Pricing
    qbo_path = Path(resp_data["pipeline_result"]["qbo_csv_path"])
    assert qbo_path.exists()
    
    # Check pricing in the CSV
    content = qbo_path.read_text(encoding="utf-8")
    # Ensure we actually inserted priced amounts
    assert "12390.00" in content # Field Shingle Bundles
    assert "180.00" in content   # Starter Bundles
    
    # 5. Verify final state is PENDING_OPERATOR_REVIEW (not INVOICED — invoicing is now a separate manual step)
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        assert row["status"] == "PENDING_OPERATOR_REVIEW"
    finally:
        conn.close()
