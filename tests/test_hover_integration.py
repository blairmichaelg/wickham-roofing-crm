import os
import tempfile
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import get_connection
from app.api.auth import create_access_token

client = TestClient(app)


def test_eagleview_upload_endpoint_hover_file(setup_test_db):
    """Test upload endpoint with a Hover-format PDF.
    """
    pdf_path = "samples/hover-sample.pdf"
    if not os.path.exists(pdf_path):
        pdf_path = os.environ.get("HOVER_TEST_PDF")
    if not pdf_path or not os.path.exists(pdf_path):
        import pytest
        pytest.skip("hover-sample.pdf or HOVER_TEST_PDF not set or file not found — skipping live Hover integration test")
    
    job_id = "99999999-9999-9999-9999-999999999903"
    conn = get_connection()
    conn.execute("INSERT INTO jobs (id, homeowner_name, status, job_type, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                 (job_id, "Hover Test", "LEAD", "INSURANCE", "123", "City", "ST", "123", "123"))
    conn.commit()
    conn.close()

    admin_token = create_access_token(role="admin")
    
    with open(pdf_path, "rb") as f:
        files = {"file": ("hover_report.pdf", f, "application/pdf")}
        response = client.post(
            f"/api/office/jobs/{job_id}/eagleview",
            files=files,
            cookies={"auth_token": admin_token}
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"


def test_eagleview_upload_endpoint_unknown_file(setup_test_db):
    job_id = "99999999-9999-9999-9999-999999999902"
    conn = get_connection()
    conn.execute("INSERT INTO jobs (id, homeowner_name, status, job_type, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                 (job_id, "Unknown Test", "LEAD", "INSURANCE", "123", "City", "ST", "123", "123"))
    conn.commit()
    conn.close()

    admin_token = create_access_token(role="admin")
    
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4 dummy non-hover non-eagleview content")
        tmp_path = tmp.name
    
    try:
        with open(tmp_path, "rb") as f:
            files = {"file": ("dummy.pdf", f, "application/pdf")}
            response = client.post(
                f"/api/office/jobs/{job_id}/eagleview",
                files=files,
                cookies={"auth_token": admin_token}
            )

        assert response.status_code == 400
        assert "Unknown measurement PDF format" in response.json()["detail"]
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
