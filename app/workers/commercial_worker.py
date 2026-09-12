"""ARQ Background Worker: Commercial Lien & Payment Deadline Monitoring.

Monitors commercial roofing jobs nearing statutory lien filing deadlines based on
the date labor/materials were last furnished. Day thresholds are configurable defaults
requiring legal verification per contract and jurisdiction.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import get_settings
from app.core.database import get_connection
from app.core.notifications import notifier

logger = structlog.get_logger("app.workers.commercial_worker")


async def monitor_commercial_lien_deadlines(ctx: dict, job_id: str | None = None) -> dict[str, Any]:
    """Inspect commercial jobs for unreconciled invoices nearing lien filing deadlines.
    
    Args:
        ctx: ARQ worker context dict.
        job_id: Optional specific job_id to evaluate, or None to evaluate all active commercial jobs.
        
    Returns:
        dict: Summary of evaluated jobs and alerts triggered.
    """
    settings = get_settings()
    deadline_days = settings.commercial_lien_deadline_days
    warning_days = settings.commercial_lien_warning_days

    today = datetime.now(timezone.utc).date()
    alerts_dispatched = []

    conn = get_connection()
    try:
        # Query commercial jobs with last_work_date set that are not yet fully paid
        if job_id:
            cursor = conn.execute("""
                SELECT id, homeowner_name, last_work_date, status
                FROM jobs
                WHERE id = ? AND job_type = 'COMMERCIAL'
            """, (job_id,))
        else:
            cursor = conn.execute("""
                SELECT id, homeowner_name, last_work_date, status
                FROM jobs
                WHERE job_type = 'COMMERCIAL' AND last_work_date IS NOT NULL AND status != 'PAID'
            """)

        jobs = cursor.fetchall()

        for row in jobs:
            j_id = row["id"]
            cust_name = row["homeowner_name"]
            l_work_str = row["last_work_date"]

            if not l_work_str:
                continue

            try:
                last_work_date = datetime.strptime(l_work_str[:10], "%Y-%m-%d").date()
            except ValueError:
                logger.warning("invalid_last_work_date_format", job_id=j_id, date_str=l_work_str)
                continue

            days_since_work = (today - last_work_date).days
            days_remaining = deadline_days - days_since_work

            # Check for unreconciled progress billing applications
            unreconciled_apps = conn.execute("""
                SELECT id, application_no, net_payment_due_cents, total_retainage_held_cents
                FROM progress_billing_applications
                WHERE job_id = ? AND status != 'PAID' AND reconciled_at IS NULL
            """, (j_id,)).fetchall()

            total_unreconciled_cents = sum(app["net_payment_due_cents"] for app in unreconciled_apps)
            total_held_retainage_cents = sum(app["total_retainage_held_cents"] for app in unreconciled_apps)
            total_at_risk_cents = total_unreconciled_cents + total_held_retainage_cents

            # Trigger alert if within the warning window before the statutory deadline
            if 0 <= days_remaining <= warning_days and total_at_risk_cents > 0:
                alert_payload = {
                    "type": "COMMERCIAL_LIEN_WARNING",
                    "priority": "HIGH",
                    "job_id": j_id,
                    "customer": cust_name,
                    "last_work_date": str(last_work_date),
                    "days_remaining": days_remaining,
                    "deadline_days": deadline_days,
                    "unreconciled_cents": total_at_risk_cents,
                    "message": (
                        f"STATUTORY LIEN DEADLINE WARNING: Job {j_id[:8]} ({cust_name}) "
                        f"has ${total_at_risk_cents / 100:.2f} unpaid with only {days_remaining} "
                        f"days until statutory lien perfection deadline ({last_work_date} + {deadline_days} days). "
                        "Confirm requirements with legal counsel immediately."
                    ),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "target_roles": ["admin", "accounting"],
                }

                logger.warning("commercial_lien_alert_dispatched", **alert_payload)
                try:
                    await notifier.broadcast(alert_payload)
                except Exception as e:
                    logger.error("failed_to_broadcast_lien_alert", error=str(e))

                alerts_dispatched.append(alert_payload)

        return {
            "checked_jobs": len(jobs),
            "alerts_dispatched": len(alerts_dispatched),
            "alerts": alerts_dispatched,
        }

    finally:
        conn.close()
