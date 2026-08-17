"""
Unit tests for the shingle-info and claim-info routes, and their integration.
"""

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.main import app

client = TestClient(app)


def test_update_claim_info_route_success():
    """Verify that updating claim info works correctly and persists to both jobs and storm_verifications tables."""
    # Create random job ID
    job_id = str(uuid.uuid4())
    
    # Seed a job in the database
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) "
            "VALUES (?, 'John Doe', '123 Main St', 'Atlanta', 'GA', '30301', '555-0100', 'LEAD_CAPTURED')",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    # Generate a field sales token
    token = create_access_token("field")
    
    # Request payload
    payload = {
        "claim_number": "CLM-12345",
        "insurer_name": "State Farm Insurance",
        "loss_date": "2026-07-28",
        "policy_number": "POL-98765",
        "adjuster_name": "Jane Miller",
        "adjuster_phone": "555-9999",
        "adjuster_email": "jane@statefarm.com"
    }

    # Patch the backup_database function since background tasks run on client response
    with patch("app.api.office_routes.backup_database") as mock_backup:
        response = client.patch(
            f"/api/office/jobs/{job_id}/claim-info",
            json=payload,
            cookies={"auth_token": token}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["job_id"] == job_id
        
        # Verify background task backup was registered
        mock_backup.assert_called_once()

    # Query DB to verify job table updates
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        assert row["claim_number"] == "CLM-12345"
        assert row["insurer_name"] == "State Farm Insurance"
        assert row["loss_date"] == "2026-07-28"
        assert row["policy_type"] == "POL-98765"
        assert row["adjuster_name"] == "Jane Miller"
        assert row["adjuster_phone"] == "555-9999"
        assert row["adjuster_email"] == "jane@statefarm.com"

        # Verify storm_verifications table synchronization
        sv_row = conn.execute("SELECT * FROM storm_verifications WHERE job_id = ?", (job_id,)).fetchone()
        assert sv_row is not None
        assert sv_row["loss_date"] == "2026-07-28"
    finally:
        conn.close()


def test_update_shingle_info_route_success():
    """Verify that updating shingle info works correctly and persists to the jobs table."""
    job_id = str(uuid.uuid4())
    
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) "
            "VALUES (?, 'Jane Smith', '456 Oak Ave', 'Atlanta', 'GA', '30302', '555-0200', 'LEAD_CAPTURED')",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    token = create_access_token("field")
    
    payload = {
        "shingle_color": "Charcoal Black",
        "shingle_type": "Architectural Asphalt"
    }

    with patch("app.api.office_routes.backup_database") as mock_backup:
        response = client.patch(
            f"/api/office/jobs/{job_id}/shingle-info",
            json=payload,
            cookies={"auth_token": token}
        )
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["job_id"] == job_id
        mock_backup.assert_called_once()

    # Query DB to verify job table updates
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        assert row["shingle_color"] == "Charcoal Black"
        assert row["shingle_type"] == "Architectural Asphalt"
    finally:
        conn.close()


def test_update_shingle_info_route_not_found():
    """Verify that updating shingle info for a non-existent job returns 404."""
    fake_job_id = str(uuid.uuid4())
    token = create_access_token("field")
    payload = {
        "shingle_color": "Charcoal Black",
        "shingle_type": "Architectural Asphalt"
    }

    response = client.patch(
        f"/api/office/jobs/{fake_job_id}/shingle-info",
        json=payload,
        cookies={"auth_token": token}
    )
    
    assert response.status_code == 404


def test_accounting_brief_and_invoice():
    """Verify that accounting/brief and invoicing endpoints work correctly together."""
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) "
            "VALUES (?, 'Accounting Test', '789 Pine Rd', 'Atlanta', 'GA', '30303', '555-0300', 'INSTALL_COMPLETED')",
            (job_id,)
        )
        conn.execute(
            "INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, qbo_exported) "
            "VALUES (?, 100000, 80000, 30000, 40000, 0.25, 0.10, 0)",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    token = create_access_token("accounting")

    # 1. Fetch accounting brief.
    response = client.get("/api/office/accounting/brief", cookies={"auth_token": token})
    assert response.status_code == 200
    brief_data = response.json()
    assert "rows" in brief_data
    assert brief_data["qbo_ready_count"] == 0

    job_row = next((r for r in brief_data["rows"] if r["job_id"] == job_id), None)
    assert job_row is not None
    assert job_row["status"] == "INSTALL_COMPLETED"
    assert job_row["carrier_rcv"] == 800.0
    assert job_row["qbo_exported"] is False

    # 2. Transition job to INVOICED
    with patch("app.api.office_routes.backup_database") as mock_backup:
        response = client.post(
            f"/api/office/accounting/jobs/{job_id}/invoice",
            cookies={"auth_token": token}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_backup.assert_called_once()

    # 3. Verify status changed to INVOICED in brief, and it is now QBO ready
    response = client.get("/api/office/accounting/brief", cookies={"auth_token": token})
    assert response.status_code == 200
    brief_data = response.json()
    assert brief_data["qbo_ready_count"] == 1

    job_row = next((r for r in brief_data["rows"] if r["job_id"] == job_id), None)
    assert job_row is not None
    assert job_row["status"] == "INVOICED"

    # 4. Record ACV payment
    response = client.post(
        f"/api/office/accounting/jobs/{job_id}/toggle-payment",
        json={"flag": "acv_received", "amount": 500.0, "date_received": "2026-08-06"},
        cookies={"auth_token": token}
    )
    assert response.status_code == 200

    # 5. Record Supplement payment - triggers commission and status PAYMENT_RECEIVED
    class MockPool:
        async def enqueue_job(self, func, **kwargs):
            pass
    app.state.redis_pool = MockPool()
    
    response = client.post(
        f"/api/office/accounting/jobs/{job_id}/toggle-payment",
        json={"flag": "supplement_received", "amount": 300.0, "date_received": "2026-08-06"},
        cookies={"auth_token": token}
    )
    assert response.status_code == 200

    # Verify status is now PAYMENT_RECEIVED
    response = client.get("/api/office/accounting/brief", cookies={"auth_token": token})
    assert response.status_code == 200
    brief_data = response.json()
    job_row = next((r for r in brief_data["rows"] if r["job_id"] == job_id), None)
    assert job_row is not None
    assert job_row["status"] == "PAYMENT_RECEIVED"

    # 6. Mark commission paid, which transitions job to CLOSED
    with patch("app.api.office_routes.backup_database") as mock_backup:
        response = client.patch(
            f"/api/office/accounting/jobs/{job_id}/commission/paid",
            cookies={"auth_token": token}
        )
        assert response.status_code == 200
        mock_backup.assert_called_once()

    # Verify status is now CLOSED
    response = client.get("/api/office/accounting/brief", cookies={"auth_token": token})
    assert response.status_code == 200
    brief_data = response.json()
    job_row = next((r for r in brief_data["rows"] if r["job_id"] == job_id), None)
    assert job_row is not None
    assert job_row["status"] == "CLOSED"


def test_final_inspection_completed_transitions():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) "
            "VALUES (?, 'Final Inspection Test', '101 Maple Ave', 'Atlanta', 'GA', '30303', '555-9999', 'INSTALL_COMPLETED')",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    ops_token = create_access_token("operations")
    accounting_token = create_access_token("accounting")

    # 1. Transition to FINAL_INSPECTION
    response = client.patch(
        f"/api/operations/jobs/{job_id}/status",
        json={"status": "FINAL_INSPECTION"},
        headers={"X-Internal-Token": ops_token}
    )
    assert response.status_code == 200

    # 2. Transition to FINAL_INSPECTION_COMPLETED
    response = client.patch(
        f"/api/operations/jobs/{job_id}/status",
        json={"status": "FINAL_INSPECTION_COMPLETED"},
        headers={"X-Internal-Token": ops_token}
    )
    assert response.status_code == 200

    # Verify status in database
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "FINAL_INSPECTION_COMPLETED"
    finally:
        conn.close()

    # 3. Create invoice from FINAL_INSPECTION_COMPLETED
    # Seed financials first so it can render accounting brief/invoicing
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, qbo_exported) "
            "VALUES (?, 100000, 80000, 30000, 40000, 0.25, 0.10, 0)",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    with patch("app.api.office_routes.backup_database") as mock_backup:
        response = client.post(
            f"/api/office/accounting/jobs/{job_id}/invoice",
            cookies={"auth_token": accounting_token}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_backup.assert_called_once()

    # Verify status changed to INVOICED
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "INVOICED"
    finally:
        conn.close()

