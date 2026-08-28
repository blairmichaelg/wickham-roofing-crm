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
