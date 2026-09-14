"""
Tests for Phase 2: PHOTOS_UPLOADED status transition and Next Best Action integration.

Verifies:
1. Uploading a photo for a CONTINGENCY_SIGNED or RETAIL_CONTRACT_SIGNED job transitions
   status to PHOTOS_UPLOADED.
2. Next Best Action no longer suggests "Complete Forensic Photo Inspection" after the transition,
   and instead suggests "Upload EagleView & Carrier SoL".
3. Uploading photos to a job already further along in the pipeline (e.g. STATEMENT_OF_LOSS_RECEIVED)
   does NOT regress the job's status.
4. ARQ worker process_photo_damage also ensures status is advanced to PHOTOS_UPLOADED.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import JobStatus, get_connection
from app.main import app
from app.services.next_best_action import evaluate_job_next_actions

client = TestClient(app)


# ── Fixtures & Helpers ────────────────────────────────────────────────────────

def _insert_test_job(status: str = "CONTINGENCY_SIGNED") -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO jobs (
            id, homeowner_name, address_line1, city, state, postal_code,
            phone, status, commission_ready
        ) VALUES (?, 'Photo Test Customer', '456 Elm St', 'Thomasville', 'GA', '31792',
                  '555-0144', ?, 0)
        """,
        (job_id, status),
    )
    conn.commit()
    conn.close()
    return job_id


@pytest.fixture(autouse=True)
def mock_assert_ownership():
    with patch("app.api.field.photos._check_rep_ownership") as mock:
        yield mock


@pytest.fixture(autouse=True)
def mock_redis_pool():
    class MockPool:
        async def enqueue_job(self, func, *args, **kwargs):
            self.enqueued = func
            self.args = args
            self.kwargs = kwargs

    orig_pool = getattr(app.state, "redis_pool", None)
    app.state.redis_pool = MockPool()
    try:
        yield app.state.redis_pool
    finally:
        app.state.redis_pool = orig_pool


@pytest.fixture
def field_headers():
    token = create_access_token(role="field", rep_id="rep_123")
    return {"Authorization": f"Bearer {token}", "x-internal-token": token}


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_photo_upload_advances_status_and_updates_nba(field_headers, tmp_path, monkeypatch):
    """
    Uploading a photo for a CONTINGENCY_SIGNED job must:
    1. Advance job status to PHOTOS_UPLOADED.
    2. Change Next Best Action so 'Complete Forensic Photo Inspection' is removed
       and 'Upload EagleView & Carrier SoL' is suggested.
    """
    job_id = _insert_test_job(status=JobStatus.CONTINGENCY_SIGNED.value)

    # Verify NBA before upload
    conn = get_connection()
    job_row = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    conn.close()
    actions_before = evaluate_job_next_actions(job_row)
    action_titles_before = [a["title"] for a in actions_before]
    assert "Complete Forensic Photo Inspection" in action_titles_before

    # Mock FIELD_PHOTOS_DIR
    monkeypatch.setattr("app.api.field.photos.FIELD_PHOTOS_DIR", tmp_path)

    photo_bytes = b"\xFF\xD8\xFFfake_jpeg_content"
    resp = client.post(
        f"/api/field/jobs/{job_id}/photos",
        headers=field_headers,
        files={"file": ("elevation_front.jpg", photo_bytes, "image/jpeg")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"

    # Verify DB status advanced
    conn = get_connection()
    updated_job = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    conn.close()

    assert updated_job["status"] == JobStatus.PHOTOS_UPLOADED.value

    # Verify NBA after upload
    actions_after = evaluate_job_next_actions(updated_job)
    action_titles_after = [a["title"] for a in actions_after]
    assert "Complete Forensic Photo Inspection" not in action_titles_after
    assert "Upload EagleView & Carrier SoL" in action_titles_after


def test_photo_upload_does_not_regress_advanced_job(field_headers, tmp_path, monkeypatch):
    """
    Uploading additional photos to a job already progressed to STATEMENT_OF_LOSS_RECEIVED
    must NOT regress status back to PHOTOS_UPLOADED.
    """
    job_id = _insert_test_job(status=JobStatus.STATEMENT_OF_LOSS_RECEIVED.value)

    monkeypatch.setattr("app.api.field.photos.FIELD_PHOTOS_DIR", tmp_path)

    photo_bytes = b"\xFF\xD8\xFFfake_jpeg_content"
    resp = client.post(
        f"/api/field/jobs/{job_id}/photos",
        headers=field_headers,
        files={"file": ("extra_damage.jpg", photo_bytes, "image/jpeg")},
    )
    assert resp.status_code == 200

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    # Must preserve the advanced status
    assert row["status"] == JobStatus.STATEMENT_OF_LOSS_RECEIVED.value


@patch("app.workers.photo_processor.get_ai_client")
def test_process_photo_damage_worker_ensures_photos_uploaded(mock_ai_client, tmp_path, monkeypatch):
    """
    If process_photo_damage runs for a CONTINGENCY_SIGNED job, it must also ensure
    the status is advanced to PHOTOS_UPLOADED.
    """
    import asyncio
    from app.core.inspection_models import DamageType, PhotoAnalysis
    from app.workers.photo_processor import process_photo_damage

    job_id = _insert_test_job(status=JobStatus.CONTINGENCY_SIGNED.value)

    monkeypatch.setattr("app.workers.photo_processor.FIELD_PHOTOS_DIR", tmp_path)
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    photo_path = job_dir / "shingle_test.jpg"
    photo_path.write_bytes(b"\xFF\xD8\xFFfake_jpeg")

    mock_analysis = MagicMock(spec=PhotoAnalysis)
    mock_analysis.confidence = 0.95
    mock_analysis.damage_type = DamageType.HAIL

    mock_ai = MagicMock()
    mock_ai.analyze_roof_photo = AsyncMock(return_value=mock_analysis)
    mock_ai_client.return_value = mock_ai

    with patch("app.core.cache.set_cached_analysis"):
        asyncio.run(process_photo_damage({}, job_id, "shingle_test.jpg"))

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()

    assert row["status"] == JobStatus.PHOTOS_UPLOADED.value
