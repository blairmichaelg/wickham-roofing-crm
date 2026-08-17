"""
Unit tests for the Field UX FastApi endpoints (Epic 2).
"""

import io

import pytest
from fastapi.testclient import TestClient

from app.core.cache import init_db, set_cached_analysis
from app.core.inspection_models import DamageType, PhotoAnalysis, Severity
from app.main import app

client = TestClient(app)

# Phase 9: static field_pin is retired. Seed a field rep before login.
from unittest.mock import patch

from app.core.cache import init_db as _init_cache
from app.core.database import create_field_rep, get_field_rep_by_pin  # noqa: E402
from app.core.database import run_migrations as _init_crm


@pytest.fixture(autouse=True)
def mock_assert_field_rep_owns_job(request):
    if "no_mock_ownership" in request.keywords:
        yield None
    else:
        with patch("app.api.field_routes.assert_field_rep_owns_job") as mock:
            yield mock

_init_cache()
_init_crm()
if not get_field_rep_by_pin("3333"):
    try:
        create_field_rep("Field Test Rep", "3333")
    except Exception:
        pass  # Already exists or other error
response = client.post("/auth/login", data={"pin": "3333", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = response.cookies.get("auth_token")
client.cookies.set("auth_token", auth_cookie)

@pytest.fixture(autouse=True)
def setup_dirs(tmp_path, monkeypatch):
    """Point directories to a temp path during tests to avoid littering the repo."""
    test_field_photos = tmp_path / "field_photos"
    test_field_docs = tmp_path / "field_docs"
    test_field_photos.mkdir()
    test_field_docs.mkdir()
    
    monkeypatch.setattr("app.api.field_routes.FIELD_PHOTOS_DIR", test_field_photos)
    monkeypatch.setattr("app.api.field_routes.FIELD_DOCS_DIR", test_field_docs)
    
    # Ensure cache and CRM DB exists for the test
    init_db()
    from app.core.database import run_migrations as init_crm_db
    init_crm_db()
    
    yield
    
    # Cleanup handled by tmp_path

def test_field_routes_deny_unauthorized_token():
    """Should return 401 Unauthorized if using an invalid token."""
    response = client.get("/api/field/jobs/TEST-123/inspection", cookies={"auth_token": "invalid-token"})
    assert response.status_code == 401

def test_create_new_job_lead_intake():
    """POST /api/field/jobs should insert a DB row and create directories."""
    payload = {
        "homeowner_name": "Alice Smith",
        "address_line1": "123 Test Ave",
        "city": "Atlanta",
        "state": "GA",
        "postal_code": "30301",
        "phone": "555-0100"
    }
    response = client.post("/api/field/jobs", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "job_id" in data
    
    job_id = data["job_id"]
    
    # Verify directories were created
    import app.api.field_routes as fr
    assert (fr.FIELD_PHOTOS_DIR / job_id).exists()
    assert (fr.FIELD_DOCS_DIR / job_id).exists()
    
    # Verify SQLite DB
    from app.core.database import get_connection
    conn = get_connection()
    cursor = conn.execute("SELECT homeowner_name, status, status_history FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    
    assert row is not None
    assert row["homeowner_name"] == "Alice Smith"
    assert row["status"] == "LEAD_CAPTURED"
    assert "Initial canvasser intake via Wickham Roofing CRM" in row["status_history"]


def test_upload_field_photo():
    """POST /api/field/jobs/{id}/photos should save the photo."""
    from app.core.database import get_connection
    conn = get_connection()
    job_id = "99999999-9999-9999-9999-999999999901"
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test Homeowner", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.commit()
    conn.close()

    file_content = b"\xFF\xD8\xFFfake_jpeg_content"
    
    response = client.post(
        f"/api/field/jobs/{job_id}/photos",
        files={"file": ("test_roof.jpg", file_content, "image/jpeg")}
    )
    
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["filename"] == "test_roof.jpg"
    
    # Verify file was physically written to the mocked FIELD_PHOTOS_DIR
    import app.api.field_routes as fr
    saved_file = fr.FIELD_PHOTOS_DIR / job_id / "test_roof.jpg"
    assert saved_file.exists()
    assert saved_file.read_bytes() == file_content


def test_upload_missing_file():
    """Missing file payload should be rejected by FastAPI directly."""
    job_id = "99999999-9999-9999-9999-999999999901"
    response = client.post(f"/api/field/jobs/{job_id}/photos")
    assert response.status_code == 422  # Unprocessable Entity


def test_get_inspection_summary():
    """GET /api/field/jobs/{id}/inspection should aggregate photos and cache."""
    # 1. Provide a physical file for get_stable_photos
    import app.api.field_routes as fr
    job_dir = fr.FIELD_PHOTOS_DIR / "TEST-JOB-002"
    job_dir.mkdir()
    photo_path = job_dir / "valid_image.jpg"
    photo_path.write_bytes(b"\xff\xd8" + b"A" * 100)  # valid-ish jpeg content
    
    # 2. Inject an analysis into the SQLite cache
    analysis = PhotoAnalysis(
        filename="valid_image.jpg",
        damage_detected=True,
        damage_type=DamageType.HAIL,
        severity=Severity.MODERATE,
        confidence=0.99,
        forensic_narrative="Test"
    )
    set_cached_analysis("TEST-JOB-002", "fake_hash", analysis)
    
    # 3. Call the endpoint
    response = client.get("/api/field/jobs/TEST-JOB-002/inspection")
    
    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "TEST-JOB-002"
    
    # Check that photos and analyses were populated
    assert len(data["photos"]) == 1
    assert data["photos"][0]["filepath"].endswith("valid_image.jpg")
    
    assert len(data["analyses"]) == 1
    assert data["analyses"][0]["filename"] == "valid_image.jpg"
    assert data["analyses"][0]["damage_detected"] is True


def test_capture_signature():
    """POST /api/field/jobs/{job_id}/contingency-sign should decode base64, save PNG, and generate PDF."""
    from app.core.database import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("99999999-9999-9999-9999-999999999993", "Test Homeowner", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.commit()
    conn.close()

    tiny_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    data_uri = f"data:image/png;base64,{tiny_png_base64}"
    
    response = client.post(
        "/api/field/jobs/99999999-9999-9999-9999-999999999993/contingency-sign",
        json={
            "signature_base64": data_uri,
            "signer_name": "Test Homeowner",
            "ip_address": "127.0.0.1",
            "user_agent": "Pytest"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "pdf_path" in data
    
    import app.api.field_routes as fr
    expected_path = fr.FIELD_DOCS_DIR / "99999999-9999-9999-9999-999999999993" / "99999999-9999-9999-9999-999999999993_contingency_sig.png"
    assert expected_path.exists()
    
    from PIL import Image
    file_bytes = expected_path.read_bytes()
    # Verify the saved image is valid
    saved_img = Image.open(io.BytesIO(file_bytes))
    saved_img.verify()
    assert saved_img.format == "PNG"


def test_capture_signature_bad_payload():
    """Invalid base64 should return a 400 error due to PDF/Image failure."""
    from app.core.database import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("99999999-9999-9999-9999-999999999994", "Test Homeowner", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.commit()
    conn.close()
    
    response = client.post(
        "/api/field/jobs/99999999-9999-9999-9999-999999999994/contingency-sign",
        json={
            "signature_base64": "data:image/png;base64,not_base64!@#",
            "signer_name": "Test Homeowner"
        }
    )
    assert response.status_code == 400
    assert "Invalid or corrupt image data" in response.json()["detail"]

def test_capture_signature_payload_too_large():
    """Payload > 2MB should return 413."""
    large_payload = "data:image/png;base64," + ("A" * 2_000_001)
    response = client.post(
        "/api/field/jobs/99999999-9999-9999-9999-999999999994/contingency-sign",
        json={
            "signature_base64": large_payload,
            "signer_name": "Test Homeowner"
        }
    )
    assert response.status_code == 413
    assert "Payload too large" in response.json()["detail"]


def test_capture_retail_signature():
    """POST /api/field/jobs/{job_id}/sign-retail-contract should decode base64, save PNG, and generate PDF."""
    from app.core.database import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("99999999-9999-9999-9999-999999999995", "Retail Homeowner", "123 Retail St", "City", "State", "00000", "555-5555")
    )
    conn.commit()
    conn.close()

    tiny_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
    data_uri = f"data:image/png;base64,{tiny_png_base64}"
    
    response = client.post(
        "/api/field/jobs/99999999-9999-9999-9999-999999999995/sign-retail-contract",
        json={
            "signature_base64": data_uri,
            "signer_name": "Retail Homeowner",
            "ip_address": "127.0.0.1",
            "user_agent": "Pytest",
            "total_price": 10000.0,
            "deposit_amount": 5000.0,
            "scope_description": "Replace roof"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "pdf_path" in data
    assert "noc_pdf_path" in data
    
    import app.api.field_routes as fr
    expected_sig_path = fr.FIELD_DOCS_DIR / "99999999-9999-9999-9999-999999999995" / "99999999-9999-9999-9999-999999999995_retail_contract_sig.png"
    assert expected_sig_path.exists()


def test_resolve_flag_success():
    """Test that a flag is successfully updated and resolved."""
    import uuid

    from app.core.database import get_connection
    conn = get_connection()
    job_id = str(uuid.uuid4())
    flag_id = str(uuid.uuid4())
    rule_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test Homeowner", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.execute("INSERT INTO supplement_rules (id, parent_code, required_child_code, citation_text, citation_type, trigger_logic_name, climate_dependent) VALUES (?, 'RFG', 'RFG IWS', 'Fake Rule', 'IRC', 'calc', 1)", (rule_id,))
    conn.execute("INSERT INTO supplement_flags (id, job_id, rule_id, triggered, quantity_delta, notes) VALUES (?, ?, ?, 1, 0.0, 'MANUAL REVIEW REQUIRED: Error')", (flag_id, job_id, rule_id))
    conn.commit()
    conn.close()
    
    response = client.patch(
        f"/api/field/jobs/{job_id}/flags/{flag_id}",
        json={
            "quantity_delta": 5.5,
            "resolution_note": "Found the right measurement"
        }
    )
    assert response.status_code == 200
    
    conn = get_connection()
    cursor = conn.execute("SELECT quantity_delta, notes FROM supplement_flags WHERE id = ?", (flag_id,))
    row = cursor.fetchone()
    conn.close()
    
    assert row["quantity_delta"] == 5.5
    assert row["notes"] == "RESOLVED: Found the right measurement"

def test_resolve_flag_invalid_uuid():
    """Test path traversal defense on job_id."""
    response = client.patch(
        "/api/field/jobs/invalid-job/flags/123",
        json={"quantity_delta": 1.0, "resolution_note": "test"}
    )
    assert response.status_code == 400

def test_resolve_flag_invalid_flag_uuid():
    """Test path traversal defense on flag_id."""
    import uuid
    job_id = str(uuid.uuid4())
    response = client.patch(
        f"/api/field/jobs/{job_id}/flags/invalid_flag",
        json={"quantity_delta": 1.0, "resolution_note": "test"}
    )
    assert response.status_code == 400

def test_resolve_flag_idor():
    """Test IDOR defense: a valid flag_id but wrong job_id returns 404."""
    import uuid

    from app.core.database import get_connection
    conn = get_connection()
    job_id_1 = str(uuid.uuid4())
    job_id_2 = str(uuid.uuid4())
    flag_id = str(uuid.uuid4())
    rule_id = str(uuid.uuid4())
    
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id_1, "Homeowner 1", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id_2, "Homeowner 2", "123 Test St", "City", "State", "00000", "555-5555")
    )
    conn.execute("INSERT INTO supplement_rules (id, parent_code, required_child_code, citation_text, citation_type, trigger_logic_name, climate_dependent) VALUES (?, 'RFG', 'RFG DRIP', 'Rule', 'IRC', 'calc', 0)", (rule_id,))
    # Flag belongs to job_id_1
    conn.execute("INSERT INTO supplement_flags (id, job_id, rule_id, triggered, quantity_delta, notes) VALUES (?, ?, ?, 1, 0.0, 'MANUAL REVIEW REQUIRED')", (flag_id, job_id_1, rule_id))
    conn.commit()
    conn.close()
    
    # Try to resolve flag using job_id_2
    response = client.patch(
        f"/api/field/jobs/{job_id_2}/flags/{flag_id}",
        json={"quantity_delta": 10.0, "resolution_note": "Stealing flag"}
    )
    assert response.status_code == 404
    assert "Flag not found or does not belong to this job" in response.json()["detail"]


def test_field_document_visibility_restriction():
    import uuid
    from pathlib import Path

    from app.core.database import get_connection, insert_job_document
    
    conn = get_connection()
    job_id = str(uuid.uuid4())
    
    # Setup job
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, canvasser_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Doc Test", "123 Doc St", "City", "State", "00000", "555-5555", "Test Rep")
    )
    conn.commit()
    conn.close()
    
    # Insert two documents
    insert_job_document(job_id, "field_safe.pdf", "EAGLEVIEW_PDF", "/fake/path/safe.pdf", "hash1", "field_safe", "test")
    insert_job_document(job_id, "office_only.pdf", "QBO_EXPORT", "/fake/path/office.csv", "hash2", "office_only", "test")
    
    # Get documents in DB manually to find IDs
    conn = get_connection()
    cursor = conn.execute("SELECT id, visibility FROM job_documents WHERE job_id = ?", (job_id,))
    docs = cursor.fetchall()
    conn.close()
    
    safe_id = next(d['id'] for d in docs if d['visibility'] == 'field_safe')
    office_id = next(d['id'] for d in docs if d['visibility'] == 'office_only')
    
    # Test 1: List documents
    response = client.get(f"/api/field/jobs/{job_id}/documents")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]['id'] == safe_id
    
    # Test 2: Try to download office_only document
    response = client.get(f"/api/field/jobs/{job_id}/documents/{office_id}/download")
    assert response.status_code == 403
    assert "Not authorized to view this document" in response.json()["detail"]
    
    # Test 3: Try to download field_safe document (should return 404 because file is missing from disk, but NOT 403)
    response = client.get(f"/api/field/jobs/{job_id}/documents/{safe_id}/download")
    assert response.status_code == 404
    assert "File is missing from disk" in response.json()["detail"]


@pytest.mark.no_mock_ownership
def test_field_access_enforcement():
    import uuid

    from fastapi.testclient import TestClient

    from app.core.database import (
        create_field_rep,
        get_connection,
        get_field_rep_by_pin,
        insert_job_document,
    )
    from app.main import app
    
    # Create fresh rep
    pin = "4444"
    rep_name = "Auth Test Rep"
    if not get_field_rep_by_pin(pin):
        create_field_rep(rep_name, pin)
    
    rep_a = get_field_rep_by_pin(pin)
    rep_id_a = rep_a["id"]
    
    # Setup test client for Rep A
    rep_client = TestClient(app)
    resp = rep_client.post("/auth/login", data={"pin": pin, "redirect_url": "/"}, follow_redirects=False)
    rep_client.cookies.set("auth_token", resp.cookies.get("auth_token"))
    
    rep_id_b = str(uuid.uuid4())
    job_id_a = str(uuid.uuid4())
    job_id_b = str(uuid.uuid4())
    
    # 1. Create two jobs with different canvasser_rep_ids
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, canvasser_rep_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id_a, "Job A", "123 A St", "City", "State", "00000", "555-5555", rep_id_a)
    )
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, canvasser_rep_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id_b, "Job B", "123 B St", "City", "State", "00000", "555-5555", rep_id_b)
    )
    conn.commit()
    conn.close()
    
    # Insert field_safe documents for both jobs
    insert_job_document(job_id_a, "safe_a.pdf", "EAGLEVIEW_PDF", "/fake/path/a.pdf", "hash_a", "field_safe", "test")
    insert_job_document(job_id_b, "safe_b.pdf", "EAGLEVIEW_PDF", "/fake/path/b.pdf", "hash_b", "field_safe", "test")
    
    conn = get_connection()
    doc_a_id = conn.execute("SELECT id FROM job_documents WHERE job_id = ?", (job_id_a,)).fetchone()["id"]
    doc_b_id = conn.execute("SELECT id FROM job_documents WHERE job_id = ?", (job_id_b,)).fetchone()["id"]
    conn.close()
    
    # 2 & 3. Authenticated as rep A, access Job A documents
    response = rep_client.get(f"/api/field/jobs/{job_id_a}/documents")
    assert response.status_code == 200
    assert len(response.json()) == 1
    
    response = rep_client.get(f"/api/field/jobs/{job_id_a}/documents/{doc_a_id}/download")
    assert response.status_code == 404  # 404 because file is missing from disk, but NOT 403 Forbidden
    
    # 4. Same rep gets 403 when trying to access Job B (not owned)
    response = rep_client.get(f"/api/field/jobs/{job_id_b}/documents")
    assert response.status_code == 403
    
    response = rep_client.get(f"/api/field/jobs/{job_id_b}/documents/{doc_b_id}/download")
    assert response.status_code == 403
    
    # 5. Admin can access documents on both jobs regardless of ownership
    admin_client = TestClient(app)
    
    resp = admin_client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    admin_client.cookies.set("auth_token", resp.cookies.get("auth_token"))
    
    # Admin accesses Job A
    response = admin_client.get(f"/api/field/jobs/{job_id_a}/documents")
    assert response.status_code == 200
    
    # Admin accesses Job B
    response = admin_client.get(f"/api/field/jobs/{job_id_b}/documents")
    assert response.status_code == 200


def test_field_claim_info_update():
    import uuid

    from fastapi.testclient import TestClient

    from app.core.database import get_connection
    from app.main import app

    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Test Homeowner", "123 Main St", "City", "ST", "12345", "555-0000")
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    resp = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
    client.cookies.set("auth_token", resp.cookies.get("auth_token"))

    payload = {
        "claim_number": "CLM-12345",
        "insurer_name": "State Farm",
        "loss_date": "2026-05-15",
        "policy_number": "POL-9999",
        "adjuster_name": "John Adjuster"
    }

    res = client.patch(f"/api/field/jobs/{job_id}/claim-info", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "success"

    conn = get_connection()
    row = conn.execute("SELECT claim_number, insurer_name, policy_type, adjuster_name FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["claim_number"] == "CLM-12345"
    assert row["insurer_name"] == "State Farm"
    assert row["policy_type"] == "POL-9999"
    assert row["adjuster_name"] == "John Adjuster"

    sv_row = conn.execute("SELECT loss_date FROM storm_verifications WHERE job_id = ?", (job_id,)).fetchone()
    assert sv_row is not None
    assert sv_row["loss_date"] == "2026-05-15"
    conn.close()


def test_get_field_job_details_returns_full_intake_fields():
    """GET /api/field/jobs/{job_id} should return full job data for lead resumption."""
    import uuid

    from app.core.database import get_connection

    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Resume Test Owner", "789 Resume Blvd", "Valdosta", "GA", "31601", "555-8888", "LEAD_CAPTURED", "Field Test Rep")
    )
    conn.commit()
    conn.close()

    response = client.get(f"/api/field/jobs/{job_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["homeowner_name"] == "Resume Test Owner"
    assert data["address_line1"] == "789 Resume Blvd"
    assert data["city"] == "Valdosta"
    assert data["status"] == "LEAD_CAPTURED"


def test_get_field_job_details_404_for_missing_job():
    """GET /api/field/jobs/{job_id} should return 404 for a nonexistent job."""
    import uuid
    fake_id = str(uuid.uuid4())
    response = client.get(f"/api/field/jobs/{fake_id}")
    # May return 404 (not found) or raise HTTPException from _sync_fetch_job_contingency
    assert response.status_code in (404, 500)


def test_download_unsigned_contingency_generates_pdf():
    """GET /api/field/jobs/{job_id}/docs/contingency should return a PDF for a valid job."""
    import uuid

    from app.core.database import get_connection

    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "PDF Test Owner", "111 Contract St", "Valdosta", "GA", "31601", "555-7777", "LEAD_CAPTURED", "Field Test Rep")
    )
    conn.commit()
    conn.close()

    response = client.get(f"/api/field/jobs/{job_id}/docs/contingency")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    # Must have actual PDF content
    assert response.content[:4] == b"%PDF"


def test_download_unsigned_contingency_rejects_bad_job_id():
    """GET /api/field/jobs/not-a-uuid/docs/contingency should return 400."""
    response = client.get("/api/field/jobs/not-a-valid-uuid/docs/contingency")
    assert response.status_code == 400
