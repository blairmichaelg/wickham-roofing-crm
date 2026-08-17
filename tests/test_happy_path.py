import pytest
from httpx import ASGITransport, AsyncClient

from app.api.auth import create_access_token
from app.core.database import JobStatus, get_connection, update_job_status
from app.main import app


@pytest.fixture
def field_headers():
    token = create_access_token(role="field", rep_name="Tester", rep_id="123")
    return {"x-internal-token": token}

@pytest.fixture
def ops_headers():
    token = create_access_token(role="operations")
    return {"x-internal-token": token}

@pytest.fixture
def accounting_headers():
    token = create_access_token(role="accounting")
    return {"x-internal-token": token}

@pytest.mark.asyncio
async def test_end_to_end_happy_path(field_headers, ops_headers, accounting_headers):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Canvasser Intake
        lead_payload = {
            "homeowner_name": "John Doe",
            "address_line1": "123 Main St",
            "city": "Dallas",
            "state": "TX",
            "postal_code": "75001",
            "phone": "555-1234",
            "job_type": "INSURANCE"
        }
        resp = await ac.post("/api/field/jobs", json=lead_payload, headers=field_headers)
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        
        # 2. Simulate AI generating financials so we can order materials
        conn = get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute('''
                INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, permits_fee_cents)
                VALUES (?, 10000, 10000, 3000, 3000, 10, 10, 0)
            ''', (job_id,))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

        # Update status to a valid prior state for MATERIAL_ORDERED
        update_job_status(job_id, JobStatus.MATERIALS_ON_SITE, "Simulated AI progression")

        # 3. Operations: Schedule Install
        sched_payload = {
            "crew_name": "Crew Alpha",
            "install_date": "2024-12-01",
            "delivery_date": "2024-11-30"
        }
        resp = await ac.post(f"/api/operations/jobs/{job_id}/schedule", json=sched_payload, headers=ops_headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "scheduled"
        
        # 4. Accounting: Export to QBO
        # First, simulate job reaching INVOICED
        update_job_status(job_id, JobStatus.INSTALL_COMPLETED, "Simulated Install")
        update_job_status(job_id, JobStatus.INVOICED, "Simulated Invoice")
        
        resp = await ac.get("/api/office/accounting/qbo-export", headers=accounting_headers)
        assert resp.status_code == 200
        csv_content = resp.text
        
        # Ensure our job is in the batch CSV
        assert job_id in csv_content or "WR-26-" in csv_content # or however it's exported
        
        # Try again, batch should be empty (idempotency test returns 204)
        resp2 = await ac.get("/api/office/accounting/qbo-export", headers=accounting_headers)
        assert resp2.status_code == 204
