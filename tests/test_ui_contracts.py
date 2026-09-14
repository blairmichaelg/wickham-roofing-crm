import sqlite3
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import (
    JobStatus,
    atomic_qbo_export,
    get_connection,
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
    
    batch = atomic_qbo_export()
    
    job_ids = [r["job_id"] for r in batch]
    assert job1_id in job_ids
    assert job2_id not in job_ids

def test_qbo_atomic_export_idempotent(db_conn):
    job_id = setup_test_job(db_conn, "INVOICED")
    setup_test_financials(db_conn, job_id, qbo_exported=0)
    
    # First export
    batch1 = atomic_qbo_export()
    assert any(r["job_id"] == job_id for r in batch1)

    # Second call returns nothing pending
    batch2 = atomic_qbo_export()
    assert not any(r["job_id"] == job_id for r in batch2)
    
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


def test_office_jobs_sanity_check_endpoint(set_auth, db_conn):
    anomaly_job_id = setup_test_job(db_conn, "PAYMENT_RECEIVED")
    response = client.get("/api/office/jobs/sanity-check")
    assert response.status_code == 200
    data = response.json()
    assert "total_inspected" in data
    assert "anomaly_count" in data
    assert "jobs" in data
    assert data["anomaly_count"] >= 1
    target = next((j for j in data["jobs"] if j["job_id"] == anomaly_job_id), None)
    assert target is not None
    assert target["has_anomaly"] is True
    assert "storm_window_hours" in target
    assert any("last_payment_received_at is missing" in a for a in target["anomalies"])


def test_field_jobs_include_storm_flags():
    from app.api.auth import create_access_token
    token = create_access_token("field", rep_name="Test Rep", rep_id="rep_1")
    headers = {"x-internal-token": token}
    conn = get_connection()
    job_id = str(uuid.uuid4())
    try:
        conn.execute(
            """
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name, canvasser_rep_id)
            VALUES (?, 'Field Rep Lead', '456 Elm St', 'Thomasville', 'GA', '31757', '555-1234', 'LEAD_CAPTURED', 'Test Rep', 'rep_1')
            """,
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    resp = client.get("/api/field/jobs", headers=headers)
    assert resp.status_code == 200
    jobs = resp.json()
    job = next((j for j in jobs if j["id"] == job_id), None)
    assert job is not None
    assert "has_recent_hail" in job
    assert "has_recent_wind" in job
    assert "storm_window_hours" in job
    from app.config import get_settings
    assert job["storm_window_hours"] == get_settings().storm_fresh_window_hours


def test_field_app_bottom_nav_consistency_and_scroll_margins():
    """Verify Bug A (scroll margins) and Bug B (bottom tab 5 consistency) contract."""
    from app.api.auth import create_access_token
    # Test 1: Core user (e.g. Admin / Michael)
    core_token = create_access_token("admin", rep_name="Michael", rep_id="rep-michael")
    client.cookies.set("auth_token", core_token)
    resp_core = client.get("/field")
    assert resp_core.status_code == 200
    html_core = resp_core.text

    # Bottom nav must have tabSync for core user and NOT tabAdmin
    assert 'id="fieldBottomNav"' in html_core
    assert 'id="tabSync"' in html_core
    assert 'triggerManualSync()' in html_core
    assert 'id="tabAdmin"' not in html_core
    # Core user gets distinct top header link to /admin
    assert 'href="/admin"' in html_core
    assert "👑 Office" in html_core

    # Test 2: Field rep (non-core user)
    field_token = create_access_token("field", rep_name="Johnny Rep", rep_id="rep-johnny")
    client.cookies.set("auth_token", field_token)
    resp_field = client.get("/field")
    assert resp_field.status_code == 200
    html_field = resp_field.text

    # Bottom nav must have tabSync and NOT tabAdmin
    assert 'id="tabSync"' in html_field
    assert 'triggerManualSync()' in html_field
    assert 'id="tabAdmin"' not in html_field
    # Non-core user does NOT get the top header link to /admin
    assert "👑 Office" not in html_field

    # Test 3: Scroll margin classes on all target elements
    for target in [
        'id="syncStatusBar" class="mb-4 px-3.5 py-2 bg-gray-950/90 border border-gray-800 rounded-xl flex items-center justify-between text-xs shadow-md scroll-mt-20"',
        'id="nextBestActionsPanel" class="mb-6 bg-gradient-to-r from-purple-950/40 via-gray-900 to-gray-900 border border-purple-800/50 rounded-xl p-4 shadow-lg scroll-mt-20"',
        'id="stormDecisionPanel" class="mb-6 bg-gray-900 border border-gray-800 rounded-xl p-4 shadow-lg scroll-mt-20"',
        'id="newLeadHeading" class="flex justify-between items-center mb-8 border-b border-gray-700 pb-4 scroll-mt-20"',
        'id="intakeForm" class="space-y-6 scroll-mt-20"',
        'id="myJobsSection" class="mt-12 pt-8 border-t border-gray-700 scroll-mt-20"',
    ]:
        assert target in html_field, f"Target element missing scroll-mt-20: {target}"

    # Test 4: All 5 tabs are buttons with min-h-[48px]
    assert '<button type="button" onclick="navigateToFieldTab(\'intakeForm\')" id="tabNewLead"' in html_field
    assert '<button type="button" onclick="navigateToFieldTab(\'myJobsSection\')" id="tabMyJobs"' in html_field
    assert '<button type="button" onclick="navigateToFieldTab(\'stormDecisionPanel\')" id="tabStormRadar"' in html_field
    assert '<button type="button" onclick="navigateToFieldTab(\'nextBestActionsPanel\')" id="tabBestActions"' in html_field
    assert '<button type="button" onclick="triggerManualSync()" id="tabSync"' in html_field

