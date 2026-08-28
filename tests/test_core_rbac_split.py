import uuid
import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.main import app

client = TestClient(app)

@pytest.fixture
def setup_job():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, "Jane Doe", "123 Maple St", "Valdosta", "GA", "31601", "229-555-0199", "LEAD_CAPTURED")
        )
        conn.commit()
    finally:
        conn.close()
    yield job_id


def test_alex_wickham_read_only_access(setup_job):
    job_id = setup_job
    
    # Generate token for Alex Wickham
    alex_token = create_access_token(role="field", rep_name="Alex Wickham", rep_id="rep-alex")
    
    # 1. GET requests should be allowed (returns 200)
    response_get = client.get(
        "/api/office/storms/targets",
        headers={"x-internal-token": alex_token}
    )
    assert response_get.status_code == 200
    
    # 2. Mutating POST requests should be blocked (returns 403)
    financials_payload = {
        "revenue": 12000,
        "materials": 4000,
        "labor": 4000,
        "carrier_rcv": 10000,
        "deductible": 1000,
        "acv_payment": 9000,
        "recoverable_depreciation": 1000,
        "overhead_pct": 0.25,
        "commission_pct": 0.10,
        "permits_fee": 0
    }
    response_post = client.post(
        f"/api/office/jobs/{job_id}/financials",
        json=financials_payload,
        headers={"x-internal-token": alex_token}
    )
    assert response_post.status_code == 403
    assert "Read-only access: contact an admin to make this change." in response_post.json()["detail"]

    # 3. Mutating PATCH requests should be blocked (returns 403)
    response_patch = client.patch(
        f"/api/operations/job/{job_id}/materials",
        json={"materials_ordered": True},
        headers={"x-internal-token": alex_token}
    )
    assert response_patch.status_code == 403
    assert "Read-only access: contact an admin to make this change." in response_patch.json()["detail"]


def test_full_access_core_members(setup_job):
    job_id = setup_job
    
    # Test each of Michael, Scott, and Debi
    for name in ["Michael", "Scott", "Debi"]:
        token = create_access_token(role="field", rep_name=name, rep_id=f"rep-{name.lower()}")
        
        # 1. GET requests allowed (returns 200)
        response_get = client.get(
            "/api/office/storms/targets",
            headers={"x-internal-token": token}
        )
        assert response_get.status_code == 200
        
        # 2. Mutating POST requests allowed (returns 200 since they bypass admin role boundaries)
        financials_payload = {
            "revenue": 12000,
            "materials": 4000,
            "labor": 4000,
            "carrier_rcv": 10000,
            "deductible": 1000,
            "acv_payment": 9000,
            "recoverable_depreciation": 1000,
            "overhead_pct": 0.25,
            "commission_pct": 0.10,
            "permits_fee": 0
        }
        response_post = client.post(
            f"/api/office/jobs/{job_id}/financials",
            json=financials_payload,
            headers={"x-internal-token": token}
        )
        assert response_post.status_code == 200


def test_standard_field_rep_access_denied(setup_job):
    job_id = setup_job
    
    # Generate token for a standard field rep
    rep_token = create_access_token(role="field", rep_name="John Doe", rep_id="rep-john")
    
    # 1. GET requests to office/admin endpoints should be blocked (returns 403)
    response_get = client.get(
        "/api/office/storms/targets",
        headers={"x-internal-token": rep_token}
    )
    assert response_get.status_code == 403
    assert "Not authorized for office access" in response_get.json()["detail"]
    
    # 2. POST requests to office/admin endpoints should be blocked (returns 403)
    financials_payload = {
        "revenue": 12000,
        "materials": 4000,
        "labor": 4000,
        "carrier_rcv": 10000,
        "deductible": 1000,
        "acv_payment": 9000,
        "recoverable_depreciation": 1000,
        "overhead_pct": 0.25,
        "commission_pct": 0.10,
        "permits_fee": 0
    }
    response_post = client.post(
        f"/api/office/jobs/{job_id}/financials",
        json=financials_payload,
        headers={"x-internal-token": rep_token}
    )
    assert response_post.status_code == 403
    assert "Not authorized for accounting access" in response_post.json()["detail"]


def test_help_page_rendering():
    # 1. Test Alex Wickham (is_core=True) sees office tabs but NOT Debi's tab
    alex_token = create_access_token(role="field", rep_name="Alex Wickham", rep_id="rep-alex")
    client.cookies.set("auth_token", alex_token)
    response = client.get("/help")
    assert response.status_code == 200
    html_content = response.text
    
    assert "Admin Guide" in html_content
    assert "Accounting Guide" in html_content
    assert "Operations Guide" in html_content
    assert "Field Guide" in html_content
    # Confirm Debi's onboarding tab is NOT rendered
    assert "Debi's Onboarding" not in html_content
    assert "content-debi" not in html_content
    
    # 2. Test standard field rep (is_core=False) does not see office tabs
    rep_token = create_access_token(role="field", rep_name="John Doe", rep_id="rep-john")
    client.cookies.set("auth_token", rep_token)
    response_rep = client.get("/help")
    assert response_rep.status_code == 200
    html_content_rep = response_rep.text
    
    assert "Admin Guide" not in html_content_rep
    assert "Accounting Guide" not in html_content_rep
    assert "Operations Guide" not in html_content_rep
    assert "Field Guide" in html_content_rep
    assert "Debi's Onboarding" not in html_content_rep


# =====================================================================
# Phase 2 — Field-Access Scope Tests (Alex Wickham method-aware bypass)
# =====================================================================

@pytest.fixture
def setup_two_reps_and_jobs():
    """Create two different field reps each with one job."""
    rep_a_id = str(uuid.uuid4())
    rep_b_id = str(uuid.uuid4())
    job_a_id = str(uuid.uuid4())
    job_b_id = str(uuid.uuid4())

    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO field_reps (id, name, pin_hash) VALUES (?, ?, ?)",
            (rep_a_id, "Rep Alpha", "fakehash_a")
        )
        conn.execute(
            "INSERT OR IGNORE INTO field_reps (id, name, pin_hash) VALUES (?, ?, ?)",
            (rep_b_id, "Rep Beta", "fakehash_b")
        )
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, "
            "canvasser_rep_id, canvasser_name, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_a_id, "Alice Owner", "100 Alpha St", "Valdosta", "GA", "31601", "229-555-0100",
             rep_a_id, "Rep Alpha", "LEAD_CAPTURED")
        )
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, "
            "canvasser_rep_id, canvasser_name, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_b_id, "Bob Owner", "200 Beta St", "Valdosta", "GA", "31601", "229-555-0200",
             rep_b_id, "Rep Beta", "LEAD_CAPTURED")
        )
        conn.commit()
    finally:
        conn.close()

    yield {
        "rep_a_id": rep_a_id, "rep_b_id": rep_b_id,
        "job_a_id": job_a_id, "job_b_id": job_b_id,
    }

    # Teardown: remove all rows inserted by this fixture so subsequent tests
    # that call get_field_rep_by_pin don't encounter invalid passlib hashes.
    conn = get_connection()
    try:
        conn.execute("DELETE FROM jobs WHERE id IN (?, ?)", (job_a_id, job_b_id))
        conn.execute("DELETE FROM field_reps WHERE id IN (?, ?)", (rep_a_id, rep_b_id))
        conn.commit()
    finally:
        conn.close()


def test_alex_wickham_field_get_other_reps_job_allowed(setup_two_reps_and_jobs):
    """Alex Wickham: GET on a job owned by another rep returns 200 (read-only bypass)."""
    data = setup_two_reps_and_jobs
    job_b_id = data["job_b_id"]  # job owned by rep_b

    alex_token = create_access_token(role="field", rep_name="Alex Wickham", rep_id="rep-alex")

    # GET /api/field/jobs/{job_id} — should be allowed
    resp = client.get(
        f"/api/field/jobs/{job_b_id}",
        headers={"x-internal-token": alex_token}
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


def test_alex_wickham_field_mutate_other_reps_job_blocked(setup_two_reps_and_jobs):
    """Alex Wickham: mutating calls on another rep's job return 403 with read-only message."""
    data = setup_two_reps_and_jobs
    job_b_id = data["job_b_id"]  # job owned by rep_b

    alex_token = create_access_token(role="field", rep_name="Alex Wickham", rep_id="rep-alex")

    # PATCH /api/field/jobs/{job_id}/claim-info — should be blocked
    resp = client.patch(
        f"/api/field/jobs/{job_b_id}/claim-info",
        json={"claim_number": "HACK-001"},
        headers={"x-internal-token": alex_token}
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    detail = resp.json().get("detail", "")
    assert "Alex Wickham has read-only privileges" in detail, f"Wrong detail: {detail}"
    assert "Read-only access: contact an admin to make this change." in detail


def test_full_access_core_field_other_reps_job_allowed(setup_two_reps_and_jobs):
    """Michael, Scott, Debi: GET and mutating calls on any job succeed (no regression)."""
    data = setup_two_reps_and_jobs
    job_b_id = data["job_b_id"]  # job owned by rep_b

    for name in ["Michael", "Scott", "Debi"]:
        token = create_access_token(role="field", rep_name=name, rep_id=f"rep-{name.lower()}")

        # GET should work
        get_resp = client.get(
            f"/api/field/jobs/{job_b_id}",
            headers={"x-internal-token": token}
        )
        assert get_resp.status_code == 200, f"{name}: GET expected 200, got {get_resp.status_code}"

        # PATCH (mutating) should also work
        patch_resp = client.patch(
            f"/api/field/jobs/{job_b_id}/claim-info",
            json={"claim_number": f"CLAIM-{name}"},
            headers={"x-internal-token": token}
        )
        assert patch_resp.status_code in (200, 422), (
            f"{name}: PATCH expected 200/422 (not 403), got {patch_resp.status_code}: {patch_resp.text}"
        )


def test_standard_field_rep_ownership_enforcement(setup_two_reps_and_jobs):
    """Standard field reps: own job allowed, other rep's job blocked. Both GET and mutating."""
    data = setup_two_reps_and_jobs
    job_a_id = data["job_a_id"]
    job_b_id = data["job_b_id"]
    rep_a_id = data["rep_a_id"]

    rep_a_token = create_access_token(role="field", rep_name="Rep Alpha", rep_id=rep_a_id)

    # GET own job — allowed
    own_get = client.get(
        f"/api/field/jobs/{job_a_id}",
        headers={"x-internal-token": rep_a_token}
    )
    assert own_get.status_code == 200, f"Own GET expected 200, got {own_get.status_code}"

    # GET other rep's job — blocked (field access enforcement)
    other_get = client.get(
        f"/api/field/jobs/{job_b_id}",
        headers={"x-internal-token": rep_a_token}
    )
    assert other_get.status_code == 403, f"Other's GET expected 403, got {other_get.status_code}"

    # PATCH other rep's job — blocked
    other_patch = client.patch(
        f"/api/field/jobs/{job_b_id}/claim-info",
        json={"claim_number": "HACK-999"},
        headers={"x-internal-token": rep_a_token}
    )
    assert other_patch.status_code == 403, f"Other's PATCH expected 403, got {other_patch.status_code}"
