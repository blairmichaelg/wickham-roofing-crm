"""
Phase 8 Test Suite — Lockdown & Intelligence Upgrade

Tests:
 1. test_hardcoded_fallback_pins_removed
 2. test_internal_api_token_removed_from_config
 3. test_failed_login_returns_redirect_with_error
 4. test_login_page_renders
 5. test_login_page_renders_error_message
 6. test_appraisal_invoked_in_enum
 7. test_appraisal_invoked_is_operator_gate
 8. test_get_aging_jobs_sla_filter
 9. test_escalation_appraisal_gate
10. test_escalation_sla_timer_reset
11. test_canvasser_reassign_route
12. test_canvasser_reassign_requires_admin
"""
import datetime
import inspect
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import (
    JobStatus,
    get_aging_jobs,
    get_connection,
)
from app.main import app

client = TestClient(app)


# ── Auth fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_cookie():
    res = client.post(
        "/auth/login",
        data={"pin": "9999", "redirect_url": "/"},
        follow_redirects=False,
    )
    client.cookies.set("auth_token", res.cookies.get("auth_token"))
    return res.cookies.get("auth_token")


@pytest.fixture(scope="module")
def field_cookie():
    # Phase 9: static field_pin is retired from auth.
    # Seed a field rep with PIN 3333 so the login succeeds.
    from app.core.database import create_field_rep, get_field_rep_by_pin
    if not get_field_rep_by_pin("3333"):
        try:
            create_field_rep("Phase8 Test Rep", "3333")
        except ValueError:
            pass  # Already exists
    res = client.post(
        "/auth/login",
        data={"pin": "3333", "redirect_url": "/"},
        follow_redirects=False,
    )
    return res.cookies.get("auth_token")


# ── Helper ────────────────────────────────────────────────────────────────────

def _insert_test_job(
    status: str = "AWAITING_CARRIER_RESPONSE",
    supplement_sent_at: str | None = None,
    escalation_sent_at: str | None = None,
    carrier_sla_days: int = 14,
) -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        """
        INSERT INTO jobs (
            id, homeowner_name, address_line1, city, state, postal_code,
            phone, status, supplement_sent_at, escalation_sent_at,
            carrier_sla_days, commission_ready, commission_pdf_path
        ) VALUES (?, 'Test Phase8', '1 Test St', 'City', 'GA', '30000',
                  '555-0000', ?, ?, ?, ?, 0, NULL)
        """,
        (
            job_id,
            status,
            supplement_sent_at,
            escalation_sent_at,
            carrier_sla_days,
        ),
    )
    conn.execute("COMMIT")
    conn.close()
    return job_id


# ── Test 1 ────────────────────────────────────────────────────────────────────

def test_hardcoded_fallback_pins_removed():
    """The fallback PIN block (9999/8888/7777/1111) must not exist in login()."""
    import app.api.auth_routes as auth_mod
    source = inspect.getsource(auth_mod.login)
    assert "9999" not in source, (
        "Hardcoded fallback PIN '9999' found in login() — must be removed!"
    )
    assert "8888" not in source, (
        "Hardcoded fallback PIN '8888' found in login() — must be removed!"
    )


# ── Test 2 ────────────────────────────────────────────────────────────────────

def test_internal_api_token_removed_from_config():
    """INTERNAL_API_TOKEN must not appear in Settings model fields."""
    from app.config import Settings
    assert "INTERNAL_API_TOKEN" not in Settings.model_fields, (
        "INTERNAL_API_TOKEN still present in Settings — it's a security hole!"
    )


# ── Test 3 ────────────────────────────────────────────────────────────────────

def test_failed_login_returns_redirect_with_error():
    """A bad PIN must redirect to /login?...&error=1 (not raise 401 JSON)."""
    res = client.post(
        "/auth/login",
        data={"pin": "0000", "redirect_url": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303, f"Expected 303, got {res.status_code}"
    location = res.headers.get("location", "")
    assert "error=1" in location, (
        f"Expected 'error=1' in redirect Location, got: {location}"
    )
    assert "/login" in location


# ── Test 4 ────────────────────────────────────────────────────────────────────

def test_login_page_renders():
    """GET /login must return 200 with the PIN entry UI."""
    res = client.get("/login")
    assert res.status_code == 200
    assert "Enter Your PIN" in res.text


# ── Test 5 ────────────────────────────────────────────────────────────────────

def test_login_page_renders_error_message():
    """GET /login?error=1 must show the Incorrect PIN message."""
    res = client.get("/login?error=1")
    assert res.status_code == 200
    assert "Incorrect PIN" in res.text


# ── Test 6 ────────────────────────────────────────────────────────────────────

def test_appraisal_invoked_in_enum():
    """APPRAISAL_INVOKED must be a valid JobStatus value."""
    assert JobStatus("APPRAISAL_INVOKED") == JobStatus.APPRAISAL_INVOKED


# ── Test 7 ────────────────────────────────────────────────────────────────────

def test_appraisal_invoked_is_operator_gate():
    """APPRAISAL_INVOKED must be in the operator gate set (workers cannot write it)."""
    assert JobStatus.is_operator_gate(JobStatus.APPRAISAL_INVOKED) is True


# ── Test 8 ────────────────────────────────────────────────────────────────────

def test_get_aging_jobs_sla_filter():
    """Only jobs genuinely over SLA (>= carrier_sla_days) must appear."""
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    old_date = (now - datetime.timedelta(days=20)).strftime("%Y-%m-%d %H:%M:%S")
    new_date = (now - datetime.timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")

    job_a = _insert_test_job(supplement_sent_at=old_date, carrier_sla_days=14)
    job_b = _insert_test_job(supplement_sent_at=new_date, carrier_sla_days=14)

    aging = get_aging_jobs()
    ids = [j["job_id"] for j in aging]

    assert job_a in ids, "Overdue job (20 days > 14 SLA) must appear in aging list"
    assert job_b not in ids, "Recent job (5 days < 14 SLA) must NOT appear"


# ── Test 9 ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_appraisal_gate():
    """Second offense (escalation_sent_at is set) must invoke APPRAISAL_INVOKED."""
    from app.workers.escalation_processor import process_escalation

    past = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=15)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    job_id = _insert_test_job(
        status="AWAITING_CARRIER_RESPONSE",
        supplement_sent_at=past,
        escalation_sent_at=past,  # Already escalated once
    )

    result = await process_escalation(ctx={}, job_id=job_id)

    assert result["status"] == "appraisal_invoked", (
        f"Expected appraisal_invoked, got: {result}"
    )

    # Verify DB state
    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["status"] == "APPRAISAL_INVOKED"


# ── Test 10 ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_sla_timer_reset():
    """First escalation must reset supplement_sent_at to NOW and set escalation_sent_at."""
    from app.workers.escalation_processor import process_escalation

    past = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(days=20)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    job_id = _insert_test_job(
        status="AWAITING_CARRIER_RESPONSE",
        supplement_sent_at=past,
        escalation_sent_at=None,  # First escalation
    )

    # Mock GeminiClient.generate_text and PDFGenerator.generate_escalation_letter
    async def _fake_generate_text(**kwargs):
        return "Mocked escalation letter body for testing."

    async def _fake_generate_letter(job, days_elapsed, narrative):
        # Write a dummy file so the hash step doesn't fail
        from pathlib import Path
        p = Path("data/field_docs") / job["id"]
        p.mkdir(parents=True, exist_ok=True)
        fp = str(p / "Escalation_Demand_Letter.pdf")
        Path(fp).write_bytes(b"%PDF-1.4 mock")
        return fp

    with (
        patch("app.workers.escalation_processor.get_ai_client") as MockAI,
        patch("app.workers.escalation_processor.PDFGenerator") as MockPDF,
    ):
        mock_ai_instance = MockAI.return_value
        mock_ai_instance.generate_text = AsyncMock(
            return_value="Mocked escalation letter body for testing."
        )
        mock_pdf_instance = MockPDF.return_value
        mock_pdf_instance.generate_escalation_letter = AsyncMock(
            side_effect=_fake_generate_letter
        )

        result = await process_escalation(ctx={}, job_id=job_id)

    assert result["status"] == "complete", f"Expected complete, got: {result}"

    # Verify SLA reset: supplement_sent_at should be within last 10 seconds
    conn = get_connection()
    row = conn.execute(
        "SELECT supplement_sent_at, escalation_sent_at FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    conn.close()

    assert row["escalation_sent_at"] is not None, "escalation_sent_at must be set"
    sent_at = datetime.datetime.strptime(
        str(row["supplement_sent_at"]), "%Y-%m-%d %H:%M:%S"
    )
    delta = (datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - sent_at).total_seconds()
    assert delta < 15, (
        f"supplement_sent_at not reset (delta={delta:.1f}s, expected < 15s)"
    )


# ── Test 11 ───────────────────────────────────────────────────────────────────

def test_canvasser_reassign_route(admin_cookie):
    """Admin can reassign canvasser_name via PATCH."""
    job_id = _insert_test_job(status="LEAD_CAPTURED")

    client.cookies.set("auth_token", admin_cookie)
    res = client.patch(
        f"/api/office/jobs/{job_id}/canvasser",
        json={"canvasser_name": "Mike B."},
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    conn = get_connection()
    row = conn.execute(
        "SELECT canvasser_name FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    conn.close()
    assert row["canvasser_name"] == "Mike B."


# ── Test 12 ───────────────────────────────────────────────────────────────────

def test_canvasser_reassign_requires_admin(field_cookie):
    """Field role must receive 403 on PATCH /canvasser."""
    job_id = _insert_test_job(status="LEAD_CAPTURED")

    res = client.patch(
        f"/api/office/jobs/{job_id}/canvasser",
        json={"canvasser_name": "Thief"},
        cookies={"auth_token": field_cookie},
    )
    assert res.status_code == 403, (
        f"Expected 403 for field role, got: {res.status_code}"
    )
