"""
app/services/storm_matching.py — Storm-to-Lead matching, opportunities queue, and metrics.

Part of Phase A: Storm Radar Canvassing Decision Engine.
Identifies existing contacts/leads in storm areas idempotently,
tracks contact attempts, and provides local performance metrics.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

from app.config import get_settings
from app.core.database import get_connection, get_db_connection
from app.core.utils import normalize_zip, now_utc_iso

logger = structlog.get_logger("app.services.storm_matching")

# Statuses that are ineligible for storm outreach
INELIGIBLE_JOB_STATUSES = {
    "CLOSED",
    "CLAIM_DENIED",
    "RETAIL_QUOTE_DECLINED",
    "PIPELINE_FAILED",
    "INSPECTION_FAILED",
}


def match_storm_opportunities(
    conn: sqlite3.Connection | None = None,
    lookback_hours: int = 168,
) -> dict[str, Any]:
    """
    Match eligible jobs against qualifying storm events within the lookback window.
    Idempotent: Uses UNIQUE(job_id, storm_event_id) constraint.
    """
    settings = get_settings()
    min_hail = settings.storm_alert_min_hail_inches
    min_wind = settings.storm_alert_min_wind_mph

    should_close = False
    if conn is None:
        conn = get_connection()
        should_close = True

    try:
        # Calculate cutoff date
        cutoff_dt = datetime.now(UTC) - timedelta(hours=lookback_hours)
        cutoff_str = cutoff_dt.strftime("%Y-%m-%d")

        # 1. Fetch qualifying storm events (defensively filter out 0/unknown events)
        event_cursor = conn.execute(
            """
            SELECT id, zipcode, event_type, event_date, hail_size_inches, wind_speed_mph, county
            FROM storm_events
            WHERE event_date >= ?
              AND (
                  (event_type = 'HAIL' AND hail_size_inches >= ?) OR
                  (event_type = 'WIND' AND wind_speed_mph >= ?) OR
                  (event_type = 'TORNADO')
              )
            ORDER BY event_date DESC
            """,
            (cutoff_str, min_hail, min_wind),
        )
        qualifying_events = [dict(row) for row in event_cursor.fetchall()]

        if not qualifying_events:
            return {
                "opportunities_matched": 0,
                "events_evaluated": 0,
                "eligible_jobs_evaluated": 0,
            }

        # 2. Fetch eligible jobs with valid ZIP codes
        placeholders = ",".join("?" for _ in INELIGIBLE_JOB_STATUSES)
        job_cursor = conn.execute(
            f"""
            SELECT id, homeowner_name, postal_code, status, canvasser_rep_id
            FROM jobs
            WHERE status NOT IN ({placeholders})
              AND postal_code IS NOT NULL
              AND TRIM(postal_code) != ''
            """,
            tuple(INELIGIBLE_JOB_STATUSES),
        )
        eligible_jobs = [dict(row) for row in job_cursor.fetchall()]

        # Build ZIP to jobs lookup
        jobs_by_zip: dict[str, list[dict[str, Any]]] = {}
        for j in eligible_jobs:
            zip_clean = normalize_zip(j["postal_code"])
            if zip_clean:
                jobs_by_zip.setdefault(zip_clean, []).append(j)

        matched_count = 0

        # 3. Match qualifying events to jobs by exact ZIP
        for event in qualifying_events:
            ev_zip = normalize_zip(event.get("zipcode", ""))
            if not ev_zip or ev_zip not in jobs_by_zip:
                continue

            # Severity summary string
            etype = event["event_type"].upper()
            if etype == "HAIL":
                sev = f"{event['hail_size_inches']:.2f}\" Hail on {event['event_date'][:10]}"
            elif etype == "WIND":
                sev = f"{int(event['wind_speed_mph'])} mph Wind on {event['event_date'][:10]}"
            else:
                sev = f"Tornado Activity on {event['event_date'][:10]}"

            matching_jobs = jobs_by_zip[ev_zip]
            for j in matching_jobs:
                opp_id = str(uuid.uuid4())
                try:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO storm_opportunities (
                            id, job_id, storm_event_id, matched_date, match_method,
                            severity_summary, targeting_window_hours, status,
                            assigned_rep_id
                        ) VALUES (?, ?, ?, ?, 'EXACT_ZIP', ?, ?, 'new', ?)
                        """,
                        (
                            opp_id,
                            j["id"],
                            event["id"],
                            now_utc_iso()[:10],
                            sev,
                            lookback_hours,
                            j.get("canvasser_rep_id"),
                        ),
                    )
                    if cursor.rowcount > 0:
                        matched_count += 1
                except sqlite3.Error as err:
                    logger.debug("opportunity_insert_skipped", error=str(err))

        logger.info(
            "storm_opportunities_matched",
            new_opportunities=matched_count,
            qualifying_events=len(qualifying_events),
            eligible_jobs=len(eligible_jobs),
        )

        return {
            "opportunities_matched": matched_count,
            "events_evaluated": len(qualifying_events),
            "eligible_jobs_evaluated": len(eligible_jobs),
        }
    finally:
        if should_close:
            conn.close()


def get_storm_opportunities(
    job_id: str | None = None,
    rep_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve filtered storm opportunities for field or office view."""
    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        query = """
            SELECT so.*, j.homeowner_name,
                j.address_line1,
                j.city,
                j.postal_code,
                j.phone,
                j.status as job_status,
                fr.name as rep_name,
                se.event_type,
                se.hail_size_inches,
                se.wind_speed_mph,
                se.county
            FROM storm_opportunities so
            JOIN jobs j ON so.job_id = j.id
            LEFT JOIN field_reps fr ON so.assigned_rep_id = fr.id
            LEFT JOIN storm_events se ON so.storm_event_id = se.id
            WHERE 1=1
        """
        params: list[Any] = []

        if job_id:
            query += " AND so.job_id = ?"
            params.append(job_id)
        if rep_id:
            query += " AND (so.assigned_rep_id = ? OR so.assigned_rep_id IS NULL)"
            params.append(rep_id)
        if status:
            query += " AND so.status = ?"
            params.append(status)

        query += " ORDER BY so.created_at DESC LIMIT ?"
        params.append(limit)

        cursor = conn.execute(query, tuple(params))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def update_storm_opportunity_status(
    opportunity_id: str,
    status: str,
    rep_id: str | None = None,
    dismissed_reason: str | None = None,
    notes: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update status of a storm opportunity (e.g. contacted, scheduled, dismissed)."""
    valid_statuses = {"new", "surfaced", "contacted", "inspection_scheduled", "dismissed"}
    if status not in valid_statuses:
        msg = f"Invalid status '{status}'. Must be one of: {valid_statuses}"
        raise ValueError(msg)

    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        now_ts = now_utc_iso()
        contacted_ts = now_ts if status in ("contacted", "inspection_scheduled") else None

        cursor = conn.execute(
            """
            UPDATE storm_opportunities
            SET status = ?,
                contacted_at = COALESCE(?, contacted_at),
                dismissed_reason = COALESCE(?, dismissed_reason),
                notes = COALESCE(?, notes),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, contacted_ts, dismissed_reason, notes, opportunity_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Storm opportunity '{opportunity_id}' not found.")

        # Re-fetch updated row
        row = conn.execute("SELECT * FROM storm_opportunities WHERE id = ?", (opportunity_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def assign_storm_opportunity(
    opportunity_id: str,
    rep_id: str | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Assign or unassign a field rep for a storm opportunity."""
    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE storm_opportunities
            SET assigned_rep_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rep_id, opportunity_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Storm opportunity '{opportunity_id}' not found.")
        row = conn.execute("SELECT * FROM storm_opportunities WHERE id = ?", (opportunity_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def log_contact_attempt(
    job_id: str,
    rep_id: str | None,
    contact_method: str,
    outcome: str,
    rep_name: str | None = None,
    notes: str | None = None,
    opportunity_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Log a manual follow-up contact attempt (CALL, TEXT, DOOR, EMAIL) without paid SMS/email."""
    method_upper = contact_method.upper()
    valid_methods = {"CALL", "TEXT", "DOOR", "EMAIL"}
    if method_upper not in valid_methods:
        msg = f"Invalid contact method '{contact_method}'. Must be one of: {valid_methods}"
        raise ValueError(msg)

    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        attempt_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO contact_attempts (
                id, job_id, opportunity_id, rep_id, rep_name,
                contact_method, outcome, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (attempt_id, job_id, opportunity_id, rep_id, rep_name, method_upper, outcome, notes),
        )

        # Advance linked storm opportunity if still 'new' or 'surfaced'
        if opportunity_id:
            conn.execute(
                """
                UPDATE storm_opportunities
                SET status = 'contacted',
                    contacted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('new', 'surfaced')
                """,
                (opportunity_id,),
            )

        row = conn.execute("SELECT * FROM contact_attempts WHERE id = ?", (attempt_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_storm_metrics(lookback_days: int = 30, db_path: str | Path | None = None) -> dict[str, Any]:
    """
    Calculate internal, local storm performance metrics without third-party SaaS.
    """
    conn = get_db_connection(db_path) if db_path else get_connection()
    try:
        settings = get_settings()
        min_hail = settings.storm_alert_min_hail_inches
        min_wind = settings.storm_alert_min_wind_mph

        # Target areas count (qualifying ZIPs in last 7 days)
        cutoff_7d = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
        t_row = conn.execute(
            """
            SELECT COUNT(DISTINCT zipcode) as target_count
            FROM storm_events
            WHERE event_date >= ?
              AND (
                  (event_type = 'HAIL' AND hail_size_inches >= ?) OR
                  (event_type = 'WIND' AND wind_speed_mph >= ?) OR
                  (event_type = 'TORNADO')
              )
            """,
            (cutoff_7d, min_hail, min_wind),
        ).fetchone()
        target_areas_count = t_row["target_count"] if t_row else 0

        # Opportunities metrics
        opp_rows = conn.execute(
            """
            SELECT
                COUNT(*) as total_surfaced,
                SUM(CASE WHEN status IN ('contacted', 'inspection_scheduled') THEN 1 ELSE 0 END) as total_contacted,
                SUM(CASE WHEN status = 'inspection_scheduled' THEN 1 ELSE 0 END) as total_scheduled,
                SUM(CASE WHEN status = 'dismissed' THEN 1 ELSE 0 END) as total_dismissed
            FROM storm_opportunities
            """
        ).fetchone()

        total_surfaced = opp_rows["total_surfaced"] if opp_rows else 0
        total_contacted = opp_rows["total_contacted"] if opp_rows else 0
        total_scheduled = opp_rows["total_scheduled"] if opp_rows else 0
        total_dismissed = opp_rows["total_dismissed"] if opp_rows else 0

        # Downstream conversions from jobs linked to storm opportunities
        conv_row = conn.execute(
            """
            SELECT
                COUNT(DISTINCT ja.job_id) as signed_count,
                COUNT(DISTINCT CASE WHEN j.status IN ('PAYMENT_RECEIVED', 'CLOSED') THEN j.id END) as closed_count
            FROM storm_opportunities so
            JOIN jobs j ON so.job_id = j.id
            LEFT JOIN job_agreements ja ON j.id = ja.job_id
            WHERE so.status != 'dismissed'
            """
        ).fetchone()

        agreements_signed = conv_row["signed_count"] if conv_row else 0
        jobs_closed = conv_row["closed_count"] if conv_row else 0

        # Contact attempts breakdown by method
        attempt_counts = conn.execute(
            """
            SELECT contact_method, COUNT(*) as count
            FROM contact_attempts
            GROUP BY contact_method
            """
        ).fetchall()
        by_method = {r["contact_method"]: r["count"] for r in attempt_counts}

        # Dismissal reasons breakdown
        dismissal_counts = conn.execute(
            """
            SELECT dismissed_reason, COUNT(*) as count
            FROM storm_opportunities
            WHERE status = 'dismissed' AND dismissed_reason IS NOT NULL
            GROUP BY dismissed_reason
            """
        ).fetchall()
        dismissal_reasons = {r["dismissed_reason"]: r["count"] for r in dismissal_counts}

        return {
            "target_areas_count": target_areas_count,
            "opportunities_surfaced": total_surfaced,
            "opportunities_contacted": total_contacted,
            "surfaced": total_surfaced,
            "contacted": total_contacted,
            "inspections_scheduled": total_scheduled,
            "opportunities_dismissed": total_dismissed,
            "agreements_signed": agreements_signed,
            "jobs_closed": jobs_closed,
            "contact_methods": by_method,
            "dismissal_reasons": dismissal_reasons,
            "qualification_rule": f"Hail >= {min_hail:.2f}\" or Wind >= {int(min_wind)} mph within {settings.storm_canvassing_radius_miles} mi",
        }
    finally:
        conn.close()
