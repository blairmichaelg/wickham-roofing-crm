"""
app/services/next_best_action.py — Deterministic Next Best Action Engine.

Derives prioritized, explainable follow-up actions for field reps and office operators.
Strictly rules-based (no LLM black boxes).
Priority Hierarchy:
1. Time-sensitive statutory compliance & lien deadlines.
2. Stalled or SLA-exceeded jobs.
3. Fresh storm opportunities (re-inspection candidates).
4. Incomplete leads / missing signatures.
5. Production & post-install milestones (materials, review/referral requests).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

from app.core.database import JobStatus, get_connection, get_db_connection

logger = structlog.get_logger("app.services.next_best_action")


def evaluate_job_next_actions(
    job: dict[str, Any],
    role: str = "field",
    active_storm_opps: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Evaluate deterministic next best actions for a single job based on its state,
    documents, dates, and linked storm opportunities.
    """
    actions: list[dict[str, Any]] = []
    job_id = job.get("id", "")
    h_name = job.get("homeowner_name") or "Homeowner"
    address = f"{job.get('address_line1', '')}, {job.get('city', '')}".strip(", ")
    status = job.get("status") or JobStatus.LEAD_CAPTURED.value
    now = datetime.now(UTC)

    # -------------------------------------------------------------------------
    # Priority 1: Compliance, Payment & Statutory Deadlines
    # -------------------------------------------------------------------------

    # A. Georgia 5-day post-denial lock check (O.C.G.A. § 10-1-393.12)
    if status == JobStatus.CLAIM_DENIED.value:
        actions.append({
            "action_id": f"act-denial-lock-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 1,
            "title": "Statutory Denial Lock Active",
            "why": "O.C.G.A. § 10-1-393.12 5-day invoicing lock active following claim denial. Review emergency tarping or appraisal option.",
            "action_type": "COMPLIANCE",
            "action_url": f"/job/{job_id}",
            "urgency": "critical",
            "suggested_script": None,
        })

    # B. Georgia Commercial Statutory Lien Warning (O.C.G.A. § 44-14-361.1)
    if job.get("job_type") == "COMMERCIAL" and job.get("last_work_date") and status != "PAID":
        try:
            last_work = datetime.strptime(str(job["last_work_date"])[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            days_elapsed = (now - last_work).days
            days_left = 90 - days_elapsed
            if days_left <= 30:
                actions.append({
                    "action_id": f"act-lien-warning-{job_id}",
                    "job_id": job_id,
                    "homeowner_name": h_name,
                    "address": address,
                    "priority": 1,
                    "title": f"Commercial Lien Deadline ({days_left}d remaining)",
                    "why": f"90-day statutory materialman lien perfection deadline approaching ({days_elapsed} days since last work).",
                    "action_type": "COMPLIANCE",
                    "action_url": f"/job/{job_id}",
                    "urgency": "critical",
                    "suggested_script": None,
                })
        except Exception:
            pass

    # C. Payment received without closed ledger
    if status == JobStatus.PAYMENT_RECEIVED.value:
        actions.append({
            "action_id": f"act-close-ledger-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 1,
            "title": "Reconcile & Close Ledger",
            "why": "Customer payments recorded. Verify QBO invoice match and close out job financials.",
            "action_type": "PAYMENT",
            "action_url": f"/job/{job_id}",
            "urgency": "high",
            "suggested_script": None,
        })

    # -------------------------------------------------------------------------
    # Priority 2: Stalled / SLA-Exceeded Jobs
    # -------------------------------------------------------------------------
    if status == JobStatus.PENDING_OPERATOR_REVIEW.value:
        actions.append({
            "action_id": f"act-operator-review-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 2,
            "title": "Office Triage Review Required",
            "why": "EagleView & Statement of Loss reconciled. Operator approval needed to compile supplement packet.",
            "action_type": "TRIAGE",
            "action_url": f"/job/{job_id}",
            "urgency": "high",
            "suggested_script": None,
        })

    if status == JobStatus.AWAITING_CARRIER_RESPONSE.value:
        supp_sent_str = job.get("supplement_sent_at")
        sla_days = job.get("carrier_sla_days") or 14
        if supp_sent_str:
            try:
                supp_sent = datetime.fromisoformat(str(supp_sent_str)[:19]).replace(tzinfo=UTC)
                if (now - supp_sent).days >= sla_days:
                    actions.append({
                        "action_id": f"act-carrier-escalation-{job_id}",
                        "job_id": job_id,
                        "homeowner_name": h_name,
                        "address": address,
                        "priority": 2,
                        "title": f"Carrier SLA Exceeded ({sla_days}d)",
                        "why": "No response from insurer since supplement submission. Send formal escalation demand.",
                        "action_type": "TRIAGE",
                        "action_url": f"/job/{job_id}",
                        "urgency": "high",
                        "suggested_script": None,
                    })
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Priority 3: Fresh Storm Opportunities
    # -------------------------------------------------------------------------
    if active_storm_opps:
        for opp in active_storm_opps:
            if opp.get("job_id") == job_id and opp.get("status") in ("new", "surfaced"):
                sev = opp.get("severity_summary") or "qualifying storm"
                date_str = opp.get("matched_date") or ""
                actions.append({
                    "action_id": f"act-storm-opp-{opp.get('id', job_id)}",
                    "job_id": job_id,
                    "homeowner_name": h_name,
                    "address": address,
                    "priority": 3,
                    "title": f"Storm Outreach ({sev})",
                    "why": f"Verified {sev} in customer area on {date_str}. Contact for complimentary damage re-inspection.",
                    "action_type": "STORM_OPPORTUNITY",
                    "action_url": f"/job/{job_id}",
                    "urgency": "medium",
                    "suggested_script": (
                        f"Hi {h_name.split()[0] if h_name else 'there'}, this is {job.get('canvasser_name') or 'Wickham Roofing'}. "
                        f"We noticed verified {sev} recorded near your home on {date_str}. "
                        f"We're in the neighborhood this week offering free roof damage assessments to protect your property."
                    ),
                })

    # -------------------------------------------------------------------------
    # Priority 4: Incomplete Leads / Unsigned Agreements
    # -------------------------------------------------------------------------
    if status == JobStatus.LEAD_CAPTURED.value:
        actions.append({
            "action_id": f"act-resume-intake-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 4,
            "title": "Resume Intake & Capture Agreement",
            "why": "Lead contact captured without agreement signature. Present Evidence Grid and secure contingency/retail signature.",
            "action_type": "SIGNATURE",
            "action_url": f"/field?job_id={job_id}&tab=sign",
            "urgency": "medium",
            "suggested_script": (
                f"Hi {h_name.split()[0] if h_name else 'there'}, following up on our recent conversation about your roof. "
                f"We have your property intake ready to proceed with a full inspection."
            ),
        })

    # -------------------------------------------------------------------------
    # Priority 5: Active Production & Post-Install Milestones
    # -------------------------------------------------------------------------
    if status in (JobStatus.CONTINGENCY_SIGNED.value, JobStatus.RETAIL_CONTRACT_SIGNED.value):
        actions.append({
            "action_id": f"act-perform-inspection-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 5,
            "title": "Complete Forensic Photo Inspection",
            "why": "Agreement signed. Capture roof elevations, slope photos, and hail/wind damage signals.",
            "action_type": "INSPECTION",
            "action_url": f"/field?job_id={job_id}&tab=photos",
            "urgency": "normal",
            "suggested_script": None,
        })
    elif status == JobStatus.PHOTOS_UPLOADED.value:
        actions.append({
            "action_id": f"act-upload-sol-ev-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 5,
            "title": "Upload EagleView & Carrier SoL",
            "why": "Photos captured. Upload measurement report PDF and Statement of Loss to initiate automated reconciliation.",
            "action_type": "SUPPLEMENT",
            "action_url": f"/job/{job_id}",
            "urgency": "normal",
            "suggested_script": None,
        })
    elif status == JobStatus.SUPPLEMENT_GENERATED.value:
        actions.append({
            "action_id": f"act-submit-supplement-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 5,
            "title": "Submit Supplement Packet",
            "why": "Supplement PDF generated with building code citations. Submit to carrier adjuster and start SLA timer.",
            "action_type": "SUPPLEMENT",
            "action_url": f"/job/{job_id}",
            "urgency": "normal",
            "suggested_script": None,
        })
    elif status in (JobStatus.SUPPLEMENT_APPROVED.value, JobStatus.SCOPE_APPROVED.value):
        actions.append({
            "action_id": f"act-order-materials-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 5,
            "title": "Generate Supplier PO & Schedule Crew",
            "why": "Claim scope approved. Synthesize ABC Supply PO bill of materials and assign install date.",
            "action_type": "PRODUCTION",
            "action_url": f"/job/{job_id}",
            "urgency": "normal",
            "suggested_script": None,
        })
    elif status == JobStatus.INSTALL_COMPLETED.value:
        actions.append({
            "action_id": f"act-request-review-{job_id}",
            "job_id": job_id,
            "homeowner_name": h_name,
            "address": address,
            "priority": 5,
            "title": "Final Punch List & Request 5★ Review",
            "why": "Installation complete. Finalize inspection report, collect certificate of completion, and send review link.",
            "action_type": "REVIEW",
            "action_url": f"/field?job_id={job_id}&tab=review",
            "urgency": "normal",
            "suggested_script": (
                f"Hi {h_name.split()[0] if h_name else 'there'}, thank you for choosing Wickham Roofing! "
                f"Your installation is complete and inspected. If you were happy with our crew, a quick Google review means the world to us!"
            ),
        })

    for a in actions:
        if "action_title" not in a:
            a["action_title"] = a.get("title", "")

    return actions


def get_field_best_actions(
    rep_id: str | None = None,
    limit: int = 5,
    db_path: str | Any | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve top-N deterministic next best actions for a field sales rep.
    Filtered to jobs assigned to this rep (or unassigned), sorted by priority asc.
    """
    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        # Fetch jobs assigned to rep or unassigned (active, not closed)
        query = """
            SELECT id, homeowner_name, address_line1, city, postal_code, phone,
                   status, job_type, last_work_date, canvasser_rep_id, canvasser_name,
                   supplement_sent_at, carrier_sla_days
            FROM jobs
            WHERE status NOT IN ('CLOSED', 'PIPELINE_FAILED')
        """
        params: list[Any] = []
        if rep_id:
            query += " AND (canvasser_rep_id = ? OR canvasser_rep_id IS NULL)"
            params.append(rep_id)
        query += " ORDER BY created_at DESC LIMIT 50"

        cursor = conn.execute(query, tuple(params))
        jobs = [dict(r) for r in cursor.fetchall()]

        # Fetch active storm opportunities
        opp_query = """
            SELECT id, job_id, storm_event_id, severity_summary, matched_date, status
            FROM storm_opportunities
            WHERE status IN ('new', 'surfaced')
        """
        opp_params: list[Any] = []
        if rep_id:
            opp_query += " AND (assigned_rep_id = ? OR assigned_rep_id IS NULL)"
            opp_params.append(rep_id)
        opp_cursor = conn.execute(opp_query, tuple(opp_params))
        opps = [dict(r) for r in opp_cursor.fetchall()]

        all_actions: list[dict[str, Any]] = []
        for j in jobs:
            j_actions = evaluate_job_next_actions(j, role="field", active_storm_opps=opps)
            all_actions.extend(j_actions)

        # Sort strictly by priority (1 = highest urgency)
        all_actions.sort(key=lambda a: a["priority"])
        return all_actions[:limit]
    finally:
        conn.close()


def get_office_action_triage(db_path: str | Any | None = None) -> dict[str, Any]:
    """
    Retrieve categorized action triage summary for admin and office boards.
    """
    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT id, homeowner_name, address_line1, city, postal_code, phone,
                   status, job_type, last_work_date, canvasser_rep_id, canvasser_name,
                   supplement_sent_at, carrier_sla_days
            FROM jobs
            WHERE status NOT IN ('CLOSED')
            ORDER BY created_at DESC LIMIT 100
            """
        )
        jobs = [dict(r) for r in cursor.fetchall()]

        opp_cursor = conn.execute(
            "SELECT id, job_id, storm_event_id, severity_summary, matched_date, status, assigned_rep_id FROM storm_opportunities WHERE status IN ('new', 'surfaced')"
        )
        opps = [dict(r) for r in opp_cursor.fetchall()]

        all_actions: list[dict[str, Any]] = []
        for j in jobs:
            all_actions.extend(evaluate_job_next_actions(j, role="admin", active_storm_opps=opps))

        grouped: dict[str, list[dict[str, Any]]] = {
            "compliance": [],
            "stalled_jobs": [],
            "storm_opportunities": [],
            "leads_to_close": [],
            "production_followups": [],
        }

        for a in all_actions:
            if a["priority"] == 1:
                grouped["compliance"].append(a)
            elif a["priority"] == 2:
                grouped["stalled_jobs"].append(a)
            elif a["priority"] == 3:
                grouped["storm_opportunities"].append(a)
            elif a["priority"] == 4:
                grouped["leads_to_close"].append(a)
            else:
                grouped["production_followups"].append(a)

        unassigned_opps = [o for o in opps if not o.get("assigned_rep_id")]

        return {
            "total_actions": len(all_actions),
            "critical_compliance_count": len(grouped["compliance"]),
            "stalled_count": len(grouped["stalled_jobs"]),
            "storm_opportunity_count": len(grouped["storm_opportunities"]),
            "leads_needing_signature_count": len(grouped["leads_to_close"]),
            "stalled_jobs": grouped["stalled_jobs"],
            "unassigned_storm_opportunities": unassigned_opps,
            "supplement_packets_awaiting_review": [a for a in all_actions if a.get("action_type") in ("OFFICE_REVIEW", "SUPPLEMENT")],
            "missing_production_artifacts": [a for a in all_actions if a.get("action_type") in ("MEASUREMENTS", "PRODUCTION")],
            "groups": grouped,
        }
    finally:
        conn.close()
