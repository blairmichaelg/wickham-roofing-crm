import os
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection, insert_job_document
from app.main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    # Ensure test environment uses fresh DB setup if needed
    yield

def test_operations_financials_forbidden(setup_db):
    conn = get_connection()
    try:
        job_id = "test-job-rbac-1"
        conn.execute("INSERT OR IGNORE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (job_id, "John", "123 Main", "City", "ST", "12345", "555-5555"))
        conn.commit()
    finally:
        conn.close()

    token = create_access_token(role="operations")
    
    payload = {
        "revenue": 10000,
        "materials": 3000,
        "labor": 3000,
        "carrier_rcv": 9000,
        "deductible": 1000,
        "acv_payment": 8000,
        "recoverable_depreciation": 1000,
        "overhead_pct": 0.25,
        "commission_pct": 0.10,
        "permits_fee": 0
    }
    
    response = client.post(
        f"/api/office/jobs/{job_id}/financials",
        json=payload,
        headers={"x-internal-token": token}
    )
    
    assert response.status_code == 403
    assert "Not authorized for accounting access" in response.json()["detail"]


def test_accounting_financials_allowed(setup_db):
    conn = get_connection()
    try:
        job_id = "test-job-rbac-accounting"
        conn.execute("INSERT OR IGNORE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (job_id, "Jane", "123 Main", "City", "ST", "12345", "555-5555"))
        conn.commit()
    finally:
        conn.close()

    token = create_access_token(role="accounting")
    
    payload = {
        "revenue": 10000,
        "materials": 3000,
        "labor": 3000,
        "carrier_rcv": 9000,
        "deductible": 1000,
        "acv_payment": 8000,
        "recoverable_depreciation": 1000,
        "overhead_pct": 0.25,
        "commission_pct": 0.10,
        "permits_fee": 0
    }
    
    response = client.post(
        f"/api/office/jobs/{job_id}/financials",
        json=payload,
        headers={"x-internal-token": token}
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "success"


def test_field_rep_document_access(tmp_path):
    conn = get_connection()
    job_id = "99999999-9999-9999-9999-999999999904"
    rep_id = "rep-123"
    other_job_id = "99999999-9999-9999-9999-999999999905"
    
    try:
        conn.execute("INSERT OR IGNORE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, canvasser_rep_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (job_id, "Jane", "123 Main", "City", "ST", "12345", "555-5555", rep_id))
        
        conn.execute("INSERT OR IGNORE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, canvasser_rep_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (other_job_id, "Bob", "123 Main", "City", "ST", "12345", "555-5555", "other-rep"))
                     
        conn.commit()
        
        # Create some dummy files
        field_safe_path = tmp_path / "photo.jpg"
        field_safe_path.write_bytes(b"image")
        
        office_only_path = tmp_path / "sol.pdf"
        office_only_path.write_bytes(b"pdf")
        
        doc_field_safe = insert_job_document(job_id, "photo.jpg", "image/jpeg", str(field_safe_path), None, "field_safe", "PHOTO")
        doc_office_only = insert_job_document(job_id, "sol.pdf", "application/pdf", str(office_only_path), None, "office_only", "STATEMENT_OF_LOSS")
        
        other_doc_field_safe = insert_job_document(other_job_id, "photo.jpg", "image/jpeg", str(field_safe_path), None, "field_safe", "PHOTO")
    finally:
        conn.close()

    token = create_access_token(role="field", rep_id=rep_id, rep_name="Test Rep")
    
    # 1. Field rep should access their own field_safe document
    response = client.get(
        f"/api/office/jobs/{job_id}/docs/download/{doc_field_safe}",
        headers={"x-internal-token": token}
    )
    assert response.status_code == 200
    
    # 2. Field rep should NOT access their own office_only document
    response = client.get(
        f"/api/office/jobs/{job_id}/docs/download/{doc_office_only}",
        headers={"x-internal-token": token}
    )
    assert response.status_code == 403
    assert "Not authorized to view this document" in response.json()["detail"]
    
    # 3. Field rep should NOT access another rep's field_safe document
    response = client.get(
        f"/api/office/jobs/{other_job_id}/docs/download/{other_doc_field_safe}",
        headers={"x-internal-token": token}
    )
    assert response.status_code == 403
    assert "Not authorized to access this job" in response.json()["detail"]
