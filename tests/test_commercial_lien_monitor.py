"""Tests for Task 4: Commercial lien and payment-deadline monitoring via ARQ."""

import datetime
from unittest.mock import AsyncMock, patch
import pytest

from app.core.constants import JobType
from app.core.database import get_connection, run_migrations
from app.workers.commercial_worker import monitor_commercial_lien_deadlines


@pytest.fixture(autouse=True)
def setup_db():
    run_migrations()


@pytest.mark.asyncio
async def test_commercial_lien_monitor_dispatches_alert():
    """Verify alert is triggered when a commercial job with unpaid invoices enters the statutory warning window."""
    conn = get_connection()
    try:
        # Create test commercial job with last_work_date 80 days ago
        today = datetime.date(2026, 9, 12)
        last_work = today - datetime.timedelta(days=80)  # 10 days remaining until 90-day deadline
        job_id = "comm-lien-test-01"

        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.execute("""
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, email, status, job_type, last_work_date)
            VALUES (?, 'St. Paul Commercial Properties', '100 Industrial Way', 'Thomasville', 'GA', '31792', '229-555-0100', 'billing@stpaul.com', 'INVOICED', 'COMMERCIAL', ?)
        """, (job_id, str(last_work)))

        # Add unreconciled progress billing application
        app_id = "app-comm-test-01"
        conn.execute("DELETE FROM progress_billing_applications WHERE id = ?", (app_id,))
        conn.execute("""
            INSERT INTO progress_billing_applications (
                id, job_id, application_no, billing_date, retainage_percent,
                total_completed_cents, stored_materials_cents, total_billed_cents,
                retainage_withheld_cents, retainage_released_cents, net_payment_due_cents,
                total_retainage_held_cents, status
            ) VALUES (?, ?, 1, '2026-08-01', 10.0, 5000000, 0, 5000000, 500000, 0, 4500000, 500000, 'SUBMITTED')
        """, (app_id, job_id))
    finally:
        conn.close()

    mock_broadcast = AsyncMock()
    with patch("app.workers.commercial_worker.notifier.broadcast", mock_broadcast):
        with patch("app.workers.commercial_worker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2026, 9, 12, 12, 0, tzinfo=datetime.timezone.utc)
            mock_dt.strptime = datetime.datetime.strptime
            mock_dt.timezone = datetime.timezone

            result = await monitor_commercial_lien_deadlines(ctx={}, job_id=job_id)

    assert result["checked_jobs"] == 1
    assert result["alerts_dispatched"] == 1
    assert len(result["alerts"]) == 1

    alert = result["alerts"][0]
    assert alert["type"] == "COMMERCIAL_LIEN_WARNING"
    assert alert["job_id"] == job_id
    assert alert["days_remaining"] == 10
    assert alert["unreconciled_cents"] == 5000000  # 4,500,000 net + 500,000 retainage held = 5,000,000 ($50,000)
    assert mock_broadcast.called


@pytest.mark.asyncio
async def test_commercial_lien_monitor_ignores_reconciled_or_early():
    """Verify jobs outside the warning window or marked PAID do not trigger alerts."""
    conn = get_connection()
    try:
        today = datetime.date(2026, 9, 12)
        # Job 1: Work finished only 10 days ago (80 days remaining, well outside 15-day warning)
        early_work = today - datetime.timedelta(days=10)
        job_early = "comm-lien-early-02"

        conn.execute("DELETE FROM jobs WHERE id = ?", (job_early,))
        conn.execute("""
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, email, status, job_type, last_work_date)
            VALUES (?, 'Fresh Build Corp', '200 Commercial Way', 'Thomasville', 'GA', '31792', '229-555-0200', 'build@fresh.com', 'INVOICED', 'COMMERCIAL', ?)
        """, (job_early, str(early_work)))

        conn.execute("""
            INSERT INTO progress_billing_applications (
                id, job_id, application_no, billing_date, retainage_percent,
                total_completed_cents, stored_materials_cents, total_billed_cents,
                retainage_withheld_cents, retainage_released_cents, net_payment_due_cents,
                total_retainage_held_cents, status
            ) VALUES ('app-early-02', ?, 1, '2026-09-05', 10.0, 1000000, 0, 1000000, 100000, 0, 900000, 100000, 'SUBMITTED')
        """, (job_early,))

        # Job 2: Work finished 80 days ago but already marked PAID
        paid_work = today - datetime.timedelta(days=80)
        job_paid = "comm-lien-paid-03"

        conn.execute("DELETE FROM jobs WHERE id = ?", (job_paid,))
        conn.execute("""
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, email, status, job_type, last_work_date)
            VALUES (?, 'Paid In Full LLC', '300 Plaza Blvd', 'Thomasville', 'GA', '31792', '229-555-0300', 'paid@full.com', 'INVOICED', 'COMMERCIAL', ?)
        """, (job_paid, str(paid_work)))


        conn.execute("""
            INSERT INTO progress_billing_applications (
                id, job_id, application_no, billing_date, retainage_percent,
                total_completed_cents, stored_materials_cents, total_billed_cents,
                retainage_withheld_cents, retainage_released_cents, net_payment_due_cents,
                total_retainage_held_cents, status, reconciled_at
            ) VALUES ('app-paid-03', ?, 1, '2026-08-01', 10.0, 1000000, 0, 1000000, 100000, 0, 900000, 100000, 'PAID', CURRENT_TIMESTAMP)
        """, (job_paid,))
    finally:
        conn.close()

    mock_broadcast = AsyncMock()
    with patch("app.workers.commercial_worker.notifier.broadcast", mock_broadcast):
        with patch("app.workers.commercial_worker.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2026, 9, 12, 12, 0, tzinfo=datetime.timezone.utc)
            mock_dt.strptime = datetime.datetime.strptime
            mock_dt.timezone = datetime.timezone

            # Check early job
            res_early = await monitor_commercial_lien_deadlines(ctx={}, job_id=job_early)
            assert res_early["alerts_dispatched"] == 0

            # Check paid job
            res_paid = await monitor_commercial_lien_deadlines(ctx={}, job_id=job_paid)
            assert res_paid["alerts_dispatched"] == 0

    assert not mock_broadcast.called
