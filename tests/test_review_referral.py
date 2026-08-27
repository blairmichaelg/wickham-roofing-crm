"""
Tests for review/referral tracking DB helpers and REST endpoints.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import add_referral, get_connection, request_review
from app.main import app

from app.api.auth import create_access_token

client = TestClient(app)

# Generate valid JWT tokens for tests
admin_token = create_access_token("admin")
field_token = create_access_token("field")

ADMIN_HEADERS = {"x-internal-token": admin_token}
FIELD_HEADERS = {"x-internal-token": field_token}


def _create_job(status: str = "INSTALL_COMPLETED") -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            """INSERT INTO jobs
               (id, homeowner_name, address_line1, city, state, postal_code, phone, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "Test Owner", "123 Main St", "Thomasville", "GA", "31757", "5551234567", status),
        )
        # Check if job_field_rep table exists before inserting
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_field_rep'")
        if cursor.fetchone():
            conn.execute(
                "INSERT OR IGNORE INTO job_field_rep (job_id, rep_id) VALUES (?, ?)",
                (job_id, "00000000-0000-0000-0000-000000000001"),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return job_id


@pytest.fixture(autouse=True)
def clean_jobs():
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()


class TestRequestReviewHelper:
    def test_marks_review_requested(self):
        job_id = _create_job()
        result = request_review(job_id, "Alice")
        assert result["status"] == "success"
        assert result["job_id"] == job_id
        assert "review_requested_at" in result

    def test_idempotent_repeated_calls(self):
        job_id = _create_job()
        request_review(job_id, "Alice")
        result = request_review(job_id, "Bob")  # Second call overwrites
        assert result["status"] == "success"

    def test_raises_for_unknown_job(self):
        with pytest.raises(ValueError, match="not found"):
            request_review(str(uuid.uuid4()), "Alice")

    def test_audit_appended_to_history(self):
        import json
        job_id = _create_job()
        request_review(job_id, "Alice")
        conn = get_connection()
        try:
            row = conn.execute("SELECT status_history FROM jobs WHERE id = ?", (job_id,)).fetchone()
            history = json.loads(row["status_history"] or "[]")
            statuses = [h.get("status") for h in history]
            assert "REVIEW_REQUESTED" in statuses
        finally:
            conn.close()


class TestAddReferralHelper:
    def test_adds_referral_code(self):
        job_id = _create_job()
        result = add_referral(job_id, "REF-001", "neighbor")
        assert result["status"] == "success"
        assert result["referral_code"] == "REF-001"

    def test_overwrites_existing_referral(self):
        job_id = _create_job()
        add_referral(job_id, "REF-001", "neighbor")
        add_referral(job_id, "REF-002", "google")
        conn = get_connection()
        try:
            row = conn.execute("SELECT referral_code, referral_source FROM jobs WHERE id = ?", (job_id,)).fetchone()
            assert row["referral_code"] == "REF-002"
            assert row["referral_source"] == "google"
        finally:
            conn.close()

    def test_raises_for_unknown_job(self):
        with pytest.raises(ValueError):
            add_referral(str(uuid.uuid4()), "REF-001")

    def test_whitespace_stripped(self):
        job_id = _create_job()
        add_referral(job_id, "  REF-SPACE  ", "  web  ")
        conn = get_connection()
        try:
            row = conn.execute("SELECT referral_code, referral_source FROM jobs WHERE id = ?", (job_id,)).fetchone()
            assert row["referral_code"] == "REF-SPACE"
            assert row["referral_source"] == "web"
        finally:
            conn.close()


class TestReviewReferralOfficeEndpoints:
    def test_office_request_review_requires_auth(self):
        resp = client.post(f"/api/office/jobs/{uuid.uuid4()}/request-review", json={"requested_by": "office"})
        assert resp.status_code == 401

    def test_office_request_review_returns_success(self):
        job_id = _create_job()
        resp = client.post(
            f"/api/office/jobs/{job_id}/request-review",
            json={"requested_by": "admin"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"

    def test_office_request_review_404_for_unknown(self):
        resp = client.post(
            f"/api/office/jobs/{uuid.uuid4()}/request-review",
            json={"requested_by": "admin"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 404

    def test_office_add_referral_returns_success(self):
        job_id = _create_job()
        resp = client.post(
            f"/api/office/jobs/{job_id}/referral",
            json={"referral_code": "REF-TEST", "source": "door_knock"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["referral_code"] == "REF-TEST"

    def test_office_add_referral_400_invalid_uuid(self):
        resp = client.post(
            "/api/office/jobs/not-a-uuid/referral",
            json={"referral_code": "X"},
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 400
