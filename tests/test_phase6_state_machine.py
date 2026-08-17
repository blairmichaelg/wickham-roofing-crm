from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import JobStatus, generate_invoice_id, get_connection, update_job_status
from app.main import app

client = TestClient(app)

@pytest.fixture(scope="module")
def tokens():
    res1 = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    office_token = res1.cookies.get("auth_token")
    
    res2 = client.post("/auth/login", data={"pin": "7777", "redirect_url": "/"}, follow_redirects=False)
    ops_token = res2.cookies.get("auth_token")
    
    res3 = client.post("/auth/login", data={"pin": "1111", "redirect_url": "/"}, follow_redirects=False)
    field_token = res3.cookies.get("auth_token")
    
    # Default client cookie to office_token for office routes
    client.cookies.set("auth_token", office_token)
    return {"office": office_token, "ops": ops_token, "field": field_token}

@pytest.fixture(autouse=True)
def setup_teardown_db():
    conn = get_connection()
    # Ensure invoice_sequence exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_sequence (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("INSERT OR IGNORE INTO invoice_sequence (id, last_seq) VALUES (1, 0)")
    conn.commit()
    conn.close()
    yield
    # No teardown needed, in-memory or test DB is handled by main conftest usually

def test_invoice_id_generation():
    """Test human-readable invoice ID generation (WR-YY-NNNN)."""
    inv1 = generate_invoice_id()
    inv2 = generate_invoice_id()
    assert inv1.startswith("WR-")
    assert inv2.startswith("WR-")
    assert inv1 != inv2
    # Ensure sequence increments
    seq1 = int(inv1.split("-")[2])
    seq2 = int(inv2.split("-")[2])
    assert seq2 == seq1 + 1

def test_state_machine_guard_approve_supplement():
    """Test SUPPLEMENT_APPROVED requires correct prior state."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test", JobStatus.SUPPLEMENT_GENERATED, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="ILLEGAL TRANSITION: SUPPLEMENT_APPROVED requires"):
        update_job_status(job_id, JobStatus.SUPPLEMENT_APPROVED, "Test")

def test_state_machine_guard_deny_supplement():
    """Test SUPPLEMENT_DENIED requires correct prior state."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test", JobStatus.MATERIAL_ORDERED, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="ILLEGAL TRANSITION: SUPPLEMENT_DENIED requires"):
        update_job_status(job_id, JobStatus.SUPPLEMENT_DENIED, "Test")

def test_approve_supplement_route(tokens):
    """Test API endpoint for supplement approval."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test", JobStatus.AWAITING_CARRIER_RESPONSE, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    response = client.post(f"/api/office/jobs/{job_id}/approve-supplement", json={"note": "Looks good"}, cookies={"auth_token": tokens["office"]})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    conn = get_connection()
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    conn.close()
    assert status == JobStatus.SUPPLEMENT_APPROVED

def test_deny_supplement_route_missing_payload(tokens):
    """Test API endpoint for supplement denial without text fails."""
    job_id = str(uuid4())
    response = client.post(f"/api/office/jobs/{job_id}/deny-supplement", json={}, cookies={"auth_token": tokens["office"]})
    assert response.status_code == 400
    assert "Must provide denial_text" in response.text

def test_deny_supplement_route_success(tokens):
    """Test API endpoint for supplement denial triggers worker."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test", JobStatus.AWAITING_CARRIER_RESPONSE, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    class MockPool:
        async def enqueue_job(self, func, **kwargs):
            self.enqueued = func
    
    mock_pool = MockPool()
    app.state.redis_pool = mock_pool

    response = client.post(f"/api/office/jobs/{job_id}/deny-supplement", json={"denial_text": "Not covered"}, cookies={"auth_token": tokens["office"]})
    assert response.status_code == 200
    assert response.json()["status"] == "denied_rebuttal_queued"

    conn = get_connection()
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    conn.close()
    assert status == JobStatus.SUPPLEMENT_DENIED

def test_operations_schedule_route(tokens):
    """Test API endpoint for assigning a crew."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test", JobStatus.MATERIALS_ON_SITE, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    response = client.post(
        f"/api/operations/jobs/{job_id}/schedule",
        json={"crew_name": "Alpha", "install_date": "2026-08-01"},
        cookies={"auth_token": tokens["ops"]}
    )
    assert response.status_code == 200

    conn = get_connection()
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    sched = conn.execute("SELECT crew_name FROM schedule WHERE job_id = ?", (job_id,)).fetchone()["crew_name"]
    conn.close()
    
    assert status == JobStatus.INSTALL_SCHEDULED
    assert sched == "Alpha"

def test_field_routes_retail_job_enqueue(tokens):
    """Test job creation triggers retail worker for RETAIL type."""
    class MockPool:
        async def enqueue_job(self, func, **kwargs):
            self.enqueued = func
    
    mock_pool = MockPool()
    app.state.redis_pool = mock_pool

    payload = {
        "homeowner_name": "Retail Bob",
        "address_line1": "123 Retail Ave",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "30301",
        "phone": "555-0000",
        "email": "bob@retail.com",
        "job_type": "RETAIL"
    }

    response = client.post("/api/field/jobs", json=payload, cookies={"auth_token": tokens["field"]})
    assert response.status_code == 200
    assert mock_pool.enqueued == "process_retail_quote"

def test_operations_status_update_success(tokens):
    """Test manual PATCH status transitions succeed under operations role."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test Status Update", JobStatus.INSTALL_SCHEDULED, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    response = client.patch(
        f"/api/operations/jobs/{job_id}/status",
        json={"status": "INSTALL_COMPLETED"},
        headers={"X-Internal-Token": tokens["ops"]}
    )
    assert response.status_code == 200
    assert response.json()["new_status"] == "INSTALL_COMPLETED"

    conn = get_connection()
    status = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()["status"]
    conn.close()
    assert status == JobStatus.INSTALL_COMPLETED

def test_operations_status_update_invalid_transition(tokens):
    """Test illegal manual PATCH status transitions return 400."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test Status Invalid", JobStatus.MATERIALS_ON_SITE, "[]", "123", "City", "ST", "00000", "555")
    )
    conn.commit()
    conn.close()

    response = client.patch(
        f"/api/operations/jobs/{job_id}/status",
        json={"status": "INSTALL_COMPLETED"},
        headers={"X-Internal-Token": tokens["ops"]}
    )
    assert response.status_code == 400
    assert "ILLEGAL TRANSITION" in response.json()["detail"]

def test_operations_bom_download_success(tokens):
    """Test downloading the generated BoM PDF for a job with measurements."""
    job_id = str(uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, status, status_history, address_line1, city, state, postal_code, phone, ev_total_area_sf, ev_eaves_lf, ev_valley_lf, ev_rakes_lf, ev_ridge_lf) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test BoM Download", JobStatus.MATERIALS_ON_SITE, "[]", "123", "City", "ST", "00000", "555", 2500.0, 120.0, 45.0, 80.0, 40.0)
    )
    conn.commit()
    conn.close()

    response = client.get(
        f"/api/operations/jobs/{job_id}/bom/download",
        headers={"X-Internal-Token": tokens["ops"]}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert len(response.content) > 0
