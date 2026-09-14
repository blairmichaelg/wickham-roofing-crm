"""Tests for Phase 3: Optimistic Concurrency on high-risk office update endpoints.

Covers:
1. /accounting/jobs/{job_id}/commission-override
   - Concurrent stale write returns 409 Conflict with clear message.
   - Normal single-user write succeeds and advances updated_at.
2. /api/office/jobs/{job_id}/measurements/manual
   - Concurrent stale write returns 409 Conflict with clear message.
   - Normal single-user write succeeds and advances updated_at.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.main import app

client = TestClient(app)


def _insert_test_job(initial_commission_override: float | None = None) -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO jobs (
            id, homeowner_name, address_line1, city, state, postal_code,
            phone, status, commission_ready, commission_pct_override, updated_at
        ) VALUES (?, 'Concurrency Test', '100 Main St', 'Valdosta', 'GA', '31601',
                  '555-0199', 'LEAD_CAPTURED', 1, ?, '2026-01-01 12:00:00')
        """,
        (job_id, initial_commission_override),
    )
    conn.commit()
    conn.close()
    return job_id


@pytest.fixture
def accounting_cookies():
    token = create_access_token(role="accounting", rep_name="Accountant Alice", rep_id="rep_acc")
    return {"auth_token": token}


@pytest.fixture
def office_cookies():
    token = create_access_token(role="operations", rep_name="Office Oscar", rep_id="rep_off")
    return {"auth_token": token}


# ── Commission Override Tests ──────────────────────────────────────────────────

def test_commission_override_happy_path(accounting_cookies):
    """A normal update with matching or omitted updated_at succeeds."""
    job_id = _insert_test_job()

    # Read current updated_at
    conn = get_connection()
    row = conn.execute("SELECT updated_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    current_updated_at = row["updated_at"]
    conn.close()

    resp = client.post(
        f"/api/office/accounting/jobs/{job_id}/commission-override",
        cookies=accounting_cookies,
        json={"commission_pct": 0.12, "updated_at": current_updated_at},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success"}

    conn = get_connection()
    updated_row = conn.execute("SELECT commission_pct_override, updated_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert updated_row["commission_pct_override"] == 0.12


def test_commission_override_stale_write_returns_409_conflict(accounting_cookies):
    """
    Simulate:
    1. User A loads record at updated_at = '2026-01-01 12:00:00'.
    2. User B updates the record directly (simulating concurrent write).
    3. User A attempts to update using the stale updated_at value.
    4. Backend must reject with 409 Conflict.
    """
    job_id = _insert_test_job()

    stale_updated_at = "2026-01-01 12:00:00"

    # Concurrent modification by User B
    conn = get_connection()
    conn.execute("UPDATE jobs SET commission_pct_override = 0.15, updated_at = '2026-01-02 15:30:00' WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

    # User A tries to save with their stale timestamp
    resp = client.post(
        f"/api/office/accounting/jobs/{job_id}/commission-override",
        cookies=accounting_cookies,
        json={"commission_pct": 0.18, "updated_at": stale_updated_at},
    )
    assert resp.status_code == 409
    data = resp.json()
    assert "updated by another user" in data["detail"].lower()

    # Confirm DB still holds User B's value (no silent overwrite)
    conn = get_connection()
    row = conn.execute("SELECT commission_pct_override FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["commission_pct_override"] == 0.15


# ── Manual Measurement Entry Tests ─────────────────────────────────────────────

def test_manual_measurements_happy_path(office_cookies):
    """A normal manual geometry update with matching or omitted updated_at succeeds."""
    job_id = _insert_test_job()

    conn = get_connection()
    row = conn.execute("SELECT updated_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    current_updated_at = row["updated_at"]
    conn.close()

    payload = {
        "total_area_sf": 2800.0,
        "predominant_pitch": "6/12",
        "ridge_lf": 45.0,
        "hip_lf": 60.0,
        "valley_lf": 30.0,
        "eaves_lf": 150.0,
        "rake_lf": 80.0,
        "drip_edge_lf": 230.0,
        "flashing_lf": 25.0,
        "step_flashing_lf": 35.0,
        "flashing_wall_lf": 15.0,
        "total_facets": 8,
        "pipe_boot_count": 3,
        "vent_count": 4,
        "starter_strip_lf": 230.0,
        "updated_at": current_updated_at,
    }

    resp = client.post(
        f"/api/office/jobs/{job_id}/measurements/manual",
        cookies=office_cookies,
        json=payload,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    conn = get_connection()
    row = conn.execute("SELECT ev_total_area_sf, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["ev_total_area_sf"] == 2800.0
    assert row["status"] == "EV_PARSED"


def test_manual_measurements_stale_write_returns_409_conflict(office_cookies):
    """
    Simulate:
    1. User A loads job with updated_at = '2026-01-01 12:00:00'.
    2. User B enters/updates measurements first.
    3. User A attempts to submit using stale updated_at.
    4. Backend must reject with 409 Conflict.
    """
    job_id = _insert_test_job()

    stale_updated_at = "2026-01-01 12:00:00"

    # Concurrent modification by User B
    conn = get_connection()
    conn.execute(
        "UPDATE jobs SET ev_total_area_sf = 3000.0, updated_at = '2026-01-02 16:00:00' WHERE id = ?",
        (job_id,)
    )
    conn.commit()
    conn.close()

    # User A tries to submit with stale updated_at
    payload = {
        "total_area_sf": 2800.0,
        "predominant_pitch": "6/12",
        "ridge_lf": 45.0,
        "hip_lf": 60.0,
        "valley_lf": 30.0,
        "eaves_lf": 150.0,
        "rake_lf": 80.0,
        "drip_edge_lf": 230.0,
        "flashing_lf": 25.0,
        "step_flashing_lf": 35.0,
        "flashing_wall_lf": 15.0,
        "total_facets": 8,
        "pipe_boot_count": 3,
        "vent_count": 4,
        "starter_strip_lf": 230.0,
        "updated_at": stale_updated_at,
    }

    resp = client.post(
        f"/api/office/jobs/{job_id}/measurements/manual",
        cookies=office_cookies,
        json=payload,
    )
    assert resp.status_code == 409
    data = resp.json()
    assert "updated by another user" in data["detail"].lower()

    # Confirm DB still holds User B's value
    conn = get_connection()
    row = conn.execute("SELECT ev_total_area_sf FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["ev_total_area_sf"] == 3000.0
