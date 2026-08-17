import datetime
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_aging_jobs, get_connection, toggle_payment_flag
from app.main import app, days_since

client = TestClient(app)

@pytest.fixture
def auth_cookies():
    res = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    return {"auth_token": res.cookies.get("auth_token")}

def setup_test_job(conn: sqlite3.Connection, status: str = "AWAITING_CARRIER_RESPONSE", supp_sent: str = None) -> str:
    job_id = str(uuid.uuid4())
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, supplement_sent_at, carrier_sla_days, commission_ready, commission_pdf_path)
        VALUES (?, 'Test User', '123 Test', 'City', 'ST', '12345', '555-5555', ?, ?, 14, 0, NULL)
        """,
        (job_id, status, supp_sent)
    )
    conn.execute("COMMIT")
    return job_id

# 1. Test Auth Login Success
def test_auth_login_success():
    res = client.post("/auth/login", data={"pin": "1111", "redirect_url": "/"}, follow_redirects=False)
    assert res.status_code == 303
    assert "auth_token" in res.cookies

# 2. Test Auth Login Failure — now redirects to /login?error=1 (Phase 8)
def test_auth_login_failure():
    res = client.post("/auth/login", data={"pin": "wrong", "redirect_url": "/"}, follow_redirects=False)
    # Phase 8: bad PIN returns 303 redirect to /login?error=1, not bare 401
    assert res.status_code == 303
    assert "error=1" in res.headers.get("location", "")

# 3. Test Auth Logout
def test_auth_logout():
    res = client.get("/auth/logout", follow_redirects=False)
    assert res.status_code == 303
    assert not res.cookies.get("auth_token")

# 4. Test toggle_payment_flag dual trigger
def test_toggle_payment_commission_trigger():
    conn = get_connection()
    job_id = setup_test_job(conn, status="INVOICED")
    conn.close()

    res1 = toggle_payment_flag(job_id, "acv_received")
    assert res1.get("commission_triggered") is False
    
    res2 = toggle_payment_flag(job_id, "supplement_received")
    assert res2.get("commission_triggered") is True

# 5. Test get_aging_jobs
def test_get_aging_jobs():
    conn = get_connection()
    # One job over SLA (15 days ago)
    past_date = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
    job_id_aged = setup_test_job(conn, status="AWAITING_CARRIER_RESPONSE", supp_sent=past_date)
    # One job not over SLA (1 day ago)
    recent_date = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    setup_test_job(conn, status="AWAITING_CARRIER_RESPONSE", supp_sent=recent_date)
    conn.close()

    aging_jobs = get_aging_jobs()
    job_ids = [j["job_id"] for j in aging_jobs]
    # With the Phase 8 SQL SLA filter, only jobs >= carrier_sla_days appear
    assert job_id_aged in job_ids

# 6. Test days_since jinja filter
def test_days_since_filter():
    past_date = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
    assert days_since(past_date) == 20
    assert days_since(None) == 0

# 7. Test /jobs/{job_id}/escalate route
def test_queue_escalation(auth_cookies):
    from app.services.rate_limit import reset_rate_limits
    reset_rate_limits()
    conn = get_connection()
    job_id = setup_test_job(conn)
    conn.close()

    class MockPool:
        async def enqueue_job(self, func, **kwargs):
            self.enqueued = func
            self.kwargs = kwargs
    
    app.state.redis_pool = MockPool()
    res = client.post(f"/api/office/jobs/{job_id}/escalate", cookies=auth_cookies)
    assert res.status_code == 200
    assert app.state.redis_pool.enqueued == "process_escalation"

# 8. Test /jobs/{job_id}/docs/escalation missing
def test_download_escalation_missing(auth_cookies):
    conn = get_connection()
    job_id = setup_test_job(conn)
    conn.close()

    res = client.get(f"/api/office/jobs/{job_id}/docs/escalation", cookies=auth_cookies)
    assert res.status_code == 404

# 9. Test /accounting/commissions-ready
def test_commissions_ready(auth_cookies):
    conn = get_connection()
    job_id = setup_test_job(conn)
    conn.execute("UPDATE jobs SET commission_ready = 1 WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    res = client.get("/api/office/accounting/commissions-ready", cookies=auth_cookies)
    assert res.status_code == 200
    data = res.json()
    assert any(d["job_id"] == job_id for d in data)

# 10. Test /jobs/{job_id}/docs/commission missing
def test_download_commission_missing(auth_cookies):
    conn = get_connection()
    job_id = setup_test_job(conn)
    conn.close()

    res = client.get(f"/api/office/jobs/{job_id}/docs/commission", cookies=auth_cookies)
    assert res.status_code == 404
