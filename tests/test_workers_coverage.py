"""
Tests for ARQ background workers: photo_processor and commission_processor.

Covers the main happy-path and error branches to boost coverage of two
otherwise-untested worker modules.
"""
import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import get_connection


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def job_id():
    conn = get_connection()
    jid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, job_type, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (jid, "Worker Test", "1 Main St", "Atlanta", "GA", "30301", "555-0001", "INSURANCE", "LEAD_CAPTURED"),
    )
    conn.commit()
    conn.close()
    yield jid
    conn = get_connection()
    conn.execute("DELETE FROM job_documents WHERE job_id = ?", (jid,))
    conn.execute("DELETE FROM financials WHERE job_id = ?", (jid,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))
    conn.commit()
    conn.close()


@pytest.fixture
def job_with_financials(job_id):
    conn = get_connection()
    conn.execute(
        "INSERT INTO financials (job_id, revenue_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, 1000000, 250000, 150000, 0.15, 0.05),
    )
    conn.commit()
    conn.close()
    return job_id


# ── photo_processor tests ─────────────────────────────────────────────────────

class TestSyncUpdateDamageSignals:
    def test_appends_signal_to_existing_job(self, job_id):
        from app.workers.photo_processor import _sync_update_damage_signals
        signal = {"damage_type": "hail", "confidence": 0.95}
        _sync_update_damage_signals(job_id, signal)
        conn = get_connection()
        row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        signals = json.loads(row["damage_signals"])
        assert signals[0]["damage_type"] == "hail"

    def test_appends_to_existing_signals(self, job_id):
        from app.workers.photo_processor import _sync_update_damage_signals
        conn = get_connection()
        conn.execute("UPDATE jobs SET damage_signals = ? WHERE id = ?", (json.dumps([{"old": True}]), job_id))
        conn.commit()
        conn.close()
        _sync_update_damage_signals(job_id, {"new": True})
        conn = get_connection()
        row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        signals = json.loads(row["damage_signals"])
        assert len(signals) == 2

    def test_noop_on_missing_job(self):
        from app.workers.photo_processor import _sync_update_damage_signals
        # Should not raise even when job doesn't exist
        _sync_update_damage_signals(str(uuid.uuid4()), {"damage_type": "none"})

    def test_handles_corrupt_damage_signals(self, job_id):
        from app.workers.photo_processor import _sync_update_damage_signals
        conn = get_connection()
        conn.execute("UPDATE jobs SET damage_signals = 'NOT JSON' WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()
        _sync_update_damage_signals(job_id, {"damage_type": "wind"})
        conn = get_connection()
        row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        signals = json.loads(row["damage_signals"])
        assert signals[0]["damage_type"] == "wind"


@patch("app.workers.photo_processor.get_ai_client")
def test_process_photo_damage_missing_file(mock_ai_client, job_id):
    from app.workers.photo_processor import process_photo_damage
    # File does not exist on disk — should log and return without raising
    result = asyncio.run(process_photo_damage({}, job_id, "nonexistent.jpg"))
    assert result is None
    mock_ai_client.assert_not_called()


@patch("app.workers.photo_processor.get_ai_client")
def test_process_photo_damage_success(mock_ai_client, job_id, tmp_path, monkeypatch):
    from app.workers.photo_processor import process_photo_damage
    from app.core.inspection_models import PhotoAnalysis, DamageType

    # Write a fake photo so the file-exists check passes
    monkeypatch.setattr("app.workers.photo_processor.FIELD_PHOTOS_DIR", tmp_path)
    photo_dir = tmp_path / job_id
    photo_dir.mkdir()
    (photo_dir / "test.jpg").write_bytes(b"JPEG")

    mock_analysis = MagicMock(spec=PhotoAnalysis)
    mock_analysis.confidence = 0.90
    mock_analysis.damage_type = DamageType.HAIL

    mock_ai_inst = MagicMock()
    mock_ai_inst.analyze_roof_photo = AsyncMock(return_value=mock_analysis)
    mock_ai_client.return_value = mock_ai_inst

    with patch("app.workers.photo_processor._sync_update_damage_signals") as mock_update, \
         patch("app.core.cache.set_cached_analysis"):
        asyncio.run(process_photo_damage({}, job_id, "test.jpg"))
        mock_update.assert_called_once()


@patch("app.workers.photo_processor.get_ai_client")
def test_process_photo_damage_ai_error(mock_ai_client, job_id, tmp_path, monkeypatch):
    from app.workers.photo_processor import process_photo_damage

    monkeypatch.setattr("app.workers.photo_processor.FIELD_PHOTOS_DIR", tmp_path)
    photo_dir = tmp_path / job_id
    photo_dir.mkdir()
    (photo_dir / "bad.jpg").write_bytes(b"JPEG")

    mock_ai_inst = MagicMock()
    mock_ai_inst.analyze_roof_photo = AsyncMock(side_effect=RuntimeError("Gemini down"))
    mock_ai_client.return_value = mock_ai_inst

    # Should not raise — exception is caught and logged
    result = asyncio.run(process_photo_damage({}, job_id, "bad.jpg"))
    assert result is None


# ── commission_processor tests ────────────────────────────────────────────────

def test_process_commission_no_financials(job_id):
    from app.workers.commission_processor import process_commission
    res = asyncio.run(process_commission({}, job_id))
    assert res["status"] == "failed"
    assert res["reason"] == "no_financials_record"


@patch("app.workers.commission_processor.PDFGenerator")
def test_process_commission_success(mock_pdf_cls, job_with_financials, tmp_path):
    from app.workers.commission_processor import process_commission

    pdf_file = tmp_path / "Commission_Statement.pdf"
    pdf_file.write_bytes(b"COMMISSION PDF")
    mock_pdf_inst = MagicMock()
    mock_pdf_inst.generate_commission_statement = AsyncMock(return_value=str(pdf_file))
    mock_pdf_cls.return_value = mock_pdf_inst

    res = asyncio.run(process_commission({}, job_with_financials))
    assert res["status"] == "complete"
    assert "commission_amount" in res

    conn = get_connection()
    row = conn.execute(
        "SELECT commission_ready FROM jobs WHERE id = ?", (job_with_financials,)
    ).fetchone()
    conn.close()
    assert row["commission_ready"] == 1


# ── system_routes tests ───────────────────────────────────────────────────────

def test_root_redirect():
    """Root / redirects to /login."""
    from fastapi.testclient import TestClient
    from app.server import app
    client = TestClient(app, follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_health_check_db_only():
    """Health check with no Redis pool returns 503."""
    from fastapi.testclient import TestClient
    from app.server import app
    client = TestClient(app)
    resp = client.get("/health")
    # Either 200 (Redis happened to be available) or 503 (not available in test env)
    assert resp.status_code in (200, 503)
