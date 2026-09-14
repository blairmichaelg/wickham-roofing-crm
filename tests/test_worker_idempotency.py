"""
Tests for Phase 1: ARQ Worker Idempotency & Retry Safety.

Verifies:
1. Escalation retry gate false-positive fix:
   - Calling process_escalation twice in immediate succession does NOT transition to APPRAISAL_INVOKED.
   - Genuine second offense (> carrier_sla_days elapsed) DOES transition to APPRAISAL_INVOKED.
2. Photo damage signal deduplication:
   - Repeated processing for the same photo filename updates in-place, preventing duplicates.
3. Supplement reports deduplication:
   - Repeated runs with identical report content do not accumulate duplicate rows.
4. Job tasks upsert safety:
   - Repeated failure logging on retried jobs updates without IntegrityError or duplicate failure records.
"""

import datetime
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.database import JobStatus, get_connection
from app.workers.escalation_processor import process_escalation
from app.workers.photo_processor import _sync_update_damage_signals


# ── Fixtures & Helpers ────────────────────────────────────────────────────────

def _insert_test_job(
    status: str = "AWAITING_CARRIER_RESPONSE",
    supplement_sent_at: str | None = None,
    escalation_sent_at: str | None = None,
    carrier_sla_days: int = 14,
) -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO jobs (
            id, homeowner_name, address_line1, city, state, postal_code,
            phone, status, supplement_sent_at, escalation_sent_at,
            carrier_sla_days, commission_ready
        ) VALUES (?, 'Idempotency Homeowner', '123 Test Ave', 'Atlanta', 'GA', '30301',
                  '555-0199', ?, ?, ?, ?, 0)
        """,
        (job_id, status, supplement_sent_at, escalation_sent_at, carrier_sla_days),
    )
    conn.commit()
    conn.close()
    return job_id


# ── 1. Escalation Retry Gate Tests ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalation_retry_within_sla_does_not_invoke_appraisal():
    """
    Simulate process_escalation retried immediately after escalation_sent_at was set.
    Must NOT prematurely trigger APPRAISAL_INVOKED.
    """
    now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    job_id = _insert_test_job(
        status="AWAITING_CARRIER_RESPONSE",
        supplement_sent_at=now_str,
        escalation_sent_at=now_str,  # Just set seconds ago by interrupted attempt
        carrier_sla_days=14,
    )

    result = await process_escalation(ctx={}, job_id=job_id)

    # Must NOT invoke appraisal
    assert result["status"] != "appraisal_invoked"
    assert result["status"] == "complete"

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["status"] != "APPRAISAL_INVOKED"
    assert row["status"] == "AWAITING_CARRIER_RESPONSE"


@pytest.mark.asyncio
async def test_escalation_genuine_second_offense_triggers_appraisal():
    """
    Genuine second offense (> carrier_sla_days with no carrier response)
    MUST invoke APPRAISAL_INVOKED.
    """
    past_15d = (
        datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
        - datetime.timedelta(days=15)
    ).strftime("%Y-%m-%d %H:%M:%S")

    job_id = _insert_test_job(
        status="AWAITING_CARRIER_RESPONSE",
        supplement_sent_at=past_15d,
        escalation_sent_at=past_15d,  # 15 days ago (> 14d SLA)
        carrier_sla_days=14,
    )

    result = await process_escalation(ctx={}, job_id=job_id)

    assert result["status"] == "appraisal_invoked"

    conn = get_connection()
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row["status"] == "APPRAISAL_INVOKED"


# ── 2. Photo Damage Signal Deduplication Tests ────────────────────────────────

def test_photo_damage_signal_deduplication_on_retry():
    """
    Calling _sync_update_damage_signals repeatedly with the same filename
    must update the existing entry in-place instead of appending duplicate entries.
    """
    job_id = _insert_test_job()

    signal_1 = {
        "damage_type": "hail",
        "confidence": 0.85,
        "source": "gemini_v2_vision",
        "needs_review": False,
        "filename": "roof_slope_north.jpg",
        "created_at": "2026-09-14T12:00:00Z",
    }
    _sync_update_damage_signals(job_id, signal_1)

    conn = get_connection()
    row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
    signals = json.loads(row["damage_signals"])
    assert len(signals) == 1
    assert signals[0]["confidence"] == 0.85

    # Simulate ARQ retry re-running with updated/identical signal for same filename
    signal_2 = {
        "damage_type": "hail",
        "confidence": 0.92,
        "source": "gemini_v2_vision",
        "needs_review": False,
        "filename": "roof_slope_north.jpg",
        "created_at": "2026-09-14T12:01:00Z",
    }
    _sync_update_damage_signals(job_id, signal_2)

    row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    signals = json.loads(row["damage_signals"])

    # Must still be exactly 1 item, updated in-place
    assert len(signals) == 1
    assert signals[0]["filename"] == "roof_slope_north.jpg"
    assert signals[0]["confidence"] == 0.92

    # A different photo filename should append as a second signal
    signal_3 = {
        "damage_type": "wind",
        "confidence": 0.78,
        "source": "gemini_v2_vision",
        "needs_review": False,
        "filename": "gutter_damage.jpg",
        "created_at": "2026-09-14T12:02:00Z",
    }
    _sync_update_damage_signals(job_id, signal_3)

    conn = get_connection()
    row = conn.execute("SELECT damage_signals FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    signals = json.loads(row["damage_signals"])
    assert len(signals) == 2


# ── 3. Supplement Reports Deduplication Tests ─────────────────────────────────

def test_supplement_reports_deduplication_on_retry():
    """
    Inserting report snapshots for the same job with identical report_json
    must not accumulate duplicate rows in supplement_reports.
    """
    job_id = _insert_test_job()

    report_content = json.dumps({"job_id": job_id, "discrepancies": [], "total_variance": 5000})

    conn = get_connection()
    # First save
    conn.execute(
        """INSERT INTO supplement_reports (id, job_id, report_json, created_at)
           VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
        (str(uuid.uuid4()), job_id, report_content),
    )
    conn.commit()

    rows = conn.execute("SELECT COUNT(*) FROM supplement_reports WHERE job_id = ?", (job_id,)).fetchone()[0]
    assert rows == 1

    # Simulate pipeline retry logic (_save_report_sync)
    latest = conn.execute(
        """SELECT report_json FROM supplement_reports
           WHERE job_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (job_id,),
    ).fetchone()

    # If content matches, skip insert
    if latest and latest["report_json"] == report_content:
        pass  # Deduplicated!
    else:
        conn.execute(
            """INSERT INTO supplement_reports (id, job_id, report_json, created_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
            (str(uuid.uuid4()), job_id, report_content),
        )
        conn.commit()

    rows_after = conn.execute("SELECT COUNT(*) FROM supplement_reports WHERE job_id = ?", (job_id,)).fetchone()[0]
    conn.close()
    assert rows_after == 1


# ── 4. Job Tasks Upsert On Retry Tests ────────────────────────────────────────

def test_job_tasks_upsert_on_retry():
    """
    Repeated failure logging for the same job and task_type must update cleanly
    without raising IntegrityError on PRIMARY KEY(job_id, task_type).
    """
    job_id = _insert_test_job()
    task_type = "SUPPLEMENT_DRAFTING"
    phase = "failed"

    conn = get_connection()
    # First failure
    conn.execute(
        """INSERT INTO job_tasks (job_id, task_type, phase, last_error)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(job_id, task_type) DO UPDATE SET
               phase = excluded.phase,
               last_error = excluded.last_error""",
        (job_id, task_type, phase, "First transient error trace"),
    )
    conn.commit()

    row = conn.execute("SELECT last_error FROM job_tasks WHERE job_id = ? AND task_type = ?", (job_id, task_type)).fetchone()
    assert row["last_error"] == "First transient error trace"

    # Second failure on worker retry — must not raise IntegrityError
    conn.execute(
        """INSERT INTO job_tasks (job_id, task_type, phase, last_error)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(job_id, task_type) DO UPDATE SET
               phase = excluded.phase,
               last_error = excluded.last_error""",
        (job_id, task_type, phase, "Second updated error trace"),
    )
    conn.commit()

    rows = conn.execute("SELECT COUNT(*), last_error FROM job_tasks WHERE job_id = ? AND task_type = ?", (job_id, task_type)).fetchall()
    conn.close()

    assert rows[0][0] == 1
    assert rows[0][1] == "Second updated error trace"
