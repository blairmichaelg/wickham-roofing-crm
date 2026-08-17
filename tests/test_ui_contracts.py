import sqlite3
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import (
    JobStatus,
    get_connection,
    get_qbo_export_batch,
    mark_qbo_exported,
    transition_material_flags,
)
from app.main import app

client = TestClient(app)

# Bypass background tasks for testing
@pytest.fixture(autouse=True)
def mock_background_tasks(monkeypatch):
    monkeypatch.setattr("app.api.office_routes.BackgroundTasks.add_task", MagicMock())

@pytest.fixture
def set_auth():
    response = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    client.cookies.set("auth_token", response.cookies.get("auth_token"))

@pytest.fixture
def db_conn():
    conn = get_connection()
    yield conn
    conn.close()

def setup_test_job(conn: sqlite3.Connection, status: str = "MATERIAL_ORDERED") -> str:
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

def setup_test_financials(conn: sqlite3.Connection, job_id: str, qbo_exported: int = 0):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, permits_fee_cents, qbo_exported)
        VALUES (?, 100000, 100000, 10000, 10000, 10, 0, 0, ?)
        """,
        (job_id, qbo_exported)
    )
    conn.execute("COMMIT")

def test_material_flag_patch_requires_valid_uuid(set_auth):
    response = client.patch(
        "/api/operations/job/not-a-uuid/materials",
        json={"materials_ordered": True}
    )
    assert response.status_code == 400
    assert "Invalid job_id format" in response.json()["detail"]

def test_material_flag_patch_missing_both_flags(set_auth):
    job_id = str(uuid.uuid4())
    response = client.patch(
        f"/api/operations/job/{job_id}/materials",
        json={}
    )
    assert response.status_code == 422
    assert "Provide at least one flag" in response.json()["detail"]

def test_material_flag_on_site_drives_state_machine(db_conn):
    job_id = setup_test_job(db_conn, "MATERIAL_ORDERED")
    
    transition_material_flags(job_id, materials_ordered=True, materials_on_site=True)
    
    cursor = db_conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    assert row["status"] == JobStatus.MATERIALS_ON_SITE.value

def test_qbo_export_batch_excludes_already_exported(db_conn):
    job1_id = setup_test_job(db_conn, "INVOICED")
    job2_id = setup_test_job(db_conn, "INVOICED")
    
    setup_test_financials(db_conn, job1_id, qbo_exported=0)
    setup_test_financials(db_conn, job2_id, qbo_exported=1)
    
    batch = get_qbo_export_batch()
    
    job_ids = [r["job_id"] for r in batch]
    assert job1_id in job_ids
    assert job2_id not in job_ids

def test_qbo_mark_exported_idempotent(db_conn):
    job_id = setup_test_job(db_conn, "INVOICED")
    setup_test_financials(db_conn, job_id, qbo_exported=0)
    
    # Call mark twice
    mark_qbo_exported([job_id])
    mark_qbo_exported([job_id])
    
    cursor = db_conn.execute("SELECT qbo_exported FROM financials WHERE job_id = ?", (job_id,))
    row = cursor.fetchone()
    assert row["qbo_exported"] == 1

def test_admin_dashboard_renders_retail_contract_signed(set_auth, db_conn):
    _job_id = setup_test_job(db_conn, "RETAIL_CONTRACT_SIGNED")
    
    # We also need to add a few fields for rendering to work flawlessly or homeowner_name is enough.
    # The setup_test_job already inserts 'Test User' as homeowner_name and '123 Test St' as address_line1.
    
    response = client.get("/admin")
    assert response.status_code == 200
    html = response.text
    
    # We should see the job's ID (or invoice_id) or homeowner_name rendered in the HTML
    # Because job_id is random, let's verify job_id[:8] or 'Test User' is in the HTML.
    # We'll check for 'Test User' since it's the homeowner name and the job is the only one in the db.
    assert "Test User" in html
    assert "123 Test St" in html
    # Check that the badge text appears
    assert "AGREEMENT SIGNED" in html


def test_job_detail_page_exposes_inspection_report_action(set_auth, db_conn):
    job_id = setup_test_job(db_conn, "LEAD_CAPTURED")

    response = client.get(f"/office/jobs/{job_id}")
    assert response.status_code == 200
    html = response.text

    assert "Generate Homeowner Inspection Report" in html
    assert "OFFICE_TOKEN" in html
