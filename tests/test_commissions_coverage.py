import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.main import app

client = TestClient(app)

@pytest.fixture
def setup_teardown_db():
    conn = get_connection()
    try:
        # Create a job ready for commission
        job_id = str(uuid.uuid4())
        history = json.dumps([{"status": "INVOICED", "timestamp": "2026-07-30T10:00:00Z", "note": ""}])
        conn.execute('''
            INSERT INTO jobs (id, invoice_id, homeowner_name, address_line1, city, state, postal_code, phone, status, status_history, job_type, canvasser_name, commission_pct_override, commission_ready)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, 'INV-COMM-01', 'Test Commission Homeowner', '123 Test St', 'City', 'ST', '12345', '555-5555', 'INVOICED', history, 'RETAIL', 'Michael Blair', 15.0, 1))
        
        # Insert test documents
        doc_id = str(uuid.uuid4())
        conn.execute('''
            INSERT INTO job_documents (id, job_id, storage_path, filename, file_type, visibility)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (doc_id, job_id, 'fake/path.pdf', 'commission.pdf', 'COMMISSION_PDF', 'office_only'))
        
        conn.commit()
        
        yield {"job_id": job_id, "doc_id": doc_id}
        
    finally:
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()

def set_auth_cookie(role: str):
    pins = {"admin": "9999", "accounting": "8888", "operations": "7777"}
    pin = pins.get(role, "9999")
    resp = client.post("/auth/login", data={"pin": pin, "redirect_url": "/"}, follow_redirects=False)
    auth_cookie = resp.cookies.get("auth_token")
    client.cookies.set("auth_token", auth_cookie)

def test_get_commissions_ready(setup_teardown_db):
    set_auth_cookie("accounting")
    response = client.get("/api/office/accounting/commissions-ready")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    
def test_download_commission(setup_teardown_db, tmp_path):
    job_id = setup_teardown_db["job_id"]
    doc_id = setup_teardown_db["doc_id"]
    
    fake_path = tmp_path / "commission.pdf"
    fake_path.write_bytes(b"%PDF-1.4 mock pdf content")
    
    conn = get_connection()
    conn.execute("UPDATE job_documents SET storage_path = ? WHERE id = ?", (str(fake_path), doc_id))
    conn.commit()
    conn.close()
    
    set_auth_cookie("accounting")
    response = client.get(f"/api/office/jobs/{job_id}/docs/commission")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 mock pdf content"
    
def test_set_commission_override(setup_teardown_db):
    job_id = setup_teardown_db["job_id"]
    
    payload = {
        "commission_pct": 0.185
    }
    set_auth_cookie("accounting")
    response = client.post(f"/api/office/accounting/jobs/{job_id}/commission-override", json=payload)
    assert response.status_code == 200
    
    conn = get_connection()
    row = conn.execute("SELECT commission_pct_override FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    
    assert row is not None
    assert row["commission_pct_override"] == 0.185

def test_queue_escalation(setup_teardown_db):
    job_id = setup_teardown_db["job_id"]
    set_auth_cookie("admin")
    from unittest.mock import AsyncMock
    client.app.state.redis_pool = AsyncMock()
    response = client.post(f"/api/office/jobs/{job_id}/escalate")
    assert response.status_code == 200

def test_download_escalation(setup_teardown_db):
    job_id = setup_teardown_db["job_id"]
    set_auth_cookie("admin")
    response = client.get(f"/api/office/jobs/{job_id}/docs/escalation")
    assert response.status_code in [404, 200]

@pytest.mark.asyncio
async def test_generate_commission_statement(setup_teardown_db, tmp_path):
    job_id = setup_teardown_db["job_id"]
    from app.services.pdf import PDFGenerator
    generator = PDFGenerator()
    
    # We must mock get_connection and others, but we actually have a real DB with this job!
    # Wait, PDFGenerator reads from the real DB!
    job_mock = {
        "id": job_id,
        "homeowner_name": "Test Homeowner",
        "address_line1": "123 Test St",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "30301"
    }
    commission_mock = {
        "canvasser_name": "Test Rep",
        "revenue_val": 10000.0,
        "material_cost_val": 3000.0,
        "labor_cost_val": 2000.0,
        "overhead_amount": 1000.0,
        "gross_profit": 4000.0,
        "commission_pct": 0.10,
        "commission_amount": 1000.0
    }
    
    # Run it
    filepath = await generator.generate_commission_statement(job_mock, commission_mock)
    import os
    assert os.path.exists(filepath)
