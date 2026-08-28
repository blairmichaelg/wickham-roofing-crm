"""
app/core/utils.py — Pure utility functions with no I/O or framework deps.

These are thin helpers consumed across the application layer.
"""

from __future__ import annotations

from datetime import UTC, datetime


def days_since(date_str: str) -> int:
    """
    Return the number of calendar days since a date string.

    Accepts ISO-8601 strings produced by SQLite (both ``YYYY-MM-DD HH:MM:SS``
    and the extended ``YYYY-MM-DDTHH:MM:SSZ`` / ``+HH:MM`` variants).

    Returns 0 on any parse failure so callers never crash on bad data.
    """
    if not date_str:
        return 0
    try:
        date_str = date_str.removesuffix("Z")
        if "+" in date_str:
            date_str = date_str.split("+")[0]

        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

        return (datetime.now(UTC).replace(tzinfo=None) - dt.replace(tzinfo=None)).days
    except Exception:
        return 0


def calculate_speed_to_lead(status_history_str: str | None) -> float | None:
    """
    Calculate speed-to-lead as the duration (in decimal hours) from the first
    LEAD_CAPTURED event to the first subsequent qualifying sales-action event
    (CONTINGENCY_SIGNED, CLAIM_FILED, RETAIL_CONTRACT_SIGNED,
     ADJUSTER_MEETING_COMPLETED, SCOPE_APPROVED).
    
    Returns None if no qualifying event or invalid data.
    """
    if not status_history_str:
        return None
    import json
    try:
        history = json.loads(status_history_str)
        if not isinstance(history, list):
            return None
    except Exception:
        return None

    lead_captured_times = []
    qualifying_events = []

    qualifying_statuses = {
        "CONTINGENCY_SIGNED",
        "CLAIM_FILED",
        "RETAIL_CONTRACT_SIGNED",
        "ADJUSTER_MEETING_COMPLETED",
        "SCOPE_APPROVED"
    }

    for entry in history:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        ts_str = entry.get("timestamp")
        if not status or not ts_str:
            continue
        
        # Parse timestamp safely
        try:
            cleaned = ts_str.rstrip("Z")
            if "+" in cleaned:
                cleaned = cleaned.split("+")[0]
            dt = datetime.fromisoformat(cleaned)
        except Exception:
            continue

        if status == "LEAD_CAPTURED":
            lead_captured_times.append(dt)
        elif status in qualifying_statuses:
            qualifying_events.append(dt)

    if not lead_captured_times:
        return None

    # The first LEAD_CAPTURED event
    t_lead = min(lead_captured_times)

    # Find the first subsequent qualifying event (timestamp >= t_lead)
    subsequent_events = [dt for dt in qualifying_events if dt >= t_lead]
    if not subsequent_events:
        return None

    t_qualifying = min(subsequent_events)

    delta_seconds = (t_qualifying - t_lead).total_seconds()
    if delta_seconds < 0:
        return None

    return delta_seconds / 3600.0


def compute_severity_score(
    event_type: str,
    hail_size: float,
    wind_speed: float,
    age_days: float = 0.0,
    distance_miles: float | None = None,
    min_hail: float = 1.0,
    min_wind: float = 50.0,
) -> float:
    """
    Calculate a bounded severity score (0.0 to 10.0 scale) based on event metrics,
    incorporating time decay (recency) and optionally distance from office.
    """
    if event_type.upper() == "TORNADO":
        base_score = 10.0
    else:
        # Hail normalized
        hail_norm = min_hail if min_hail > 0 else 1.0
        hail_comp = (hail_size / hail_norm)
        
        # Wind normalized
        wind_norm = min_wind if min_wind > 0 else 50.0
        wind_comp = (wind_speed / wind_norm)
        
        base_score = max(hail_comp, wind_comp)
        
        # Cap non-tornado events base score at 8.0 to reserve top priority for tornadoes
        if base_score > 8.0:
            base_score = 8.0
            
    # Apply recency penalty: 5% decay per day of age, bounded to max 50% reduction
    decay = max(0.5, 1.0 - (0.05 * age_days))
    score = base_score * decay
    
    # Distance penalty: 1% penalty per 10 miles from office, capped at 10% penalty
    if distance_miles is not None:
        dist_penalty = max(0.9, 1.0 - (0.001 * distance_miles))
        score *= dist_penalty
        
    return round(score, 4)


def get_priority_info(
    severity_score: float,
    max_hail: float,
    max_wind: float,
    has_tornado: bool,
    last_event_time_utc: str = "",
) -> tuple[str, str]:
    """
    Generate priority label and reason text based on severity score and metrics.
    """
    if severity_score >= 1.5:
        label = "🔥 High"
    elif severity_score >= 1.0:
        label = "⚡ Medium"
    else:
        label = "🟢 Low"
        
    reasons = []
    if has_tornado:
        reasons.append("Tornado")
    if max_hail > 0:
        reasons.append(f"{max_hail}″ hail")
    if max_wind > 0:
        reasons.append(f"{Math.round(max_wind) if 'Math' in globals() else int(max_wind)} mph wind")
        
    # Format date/time context
    date_str = ""
    if last_event_time_utc:
        try:
            # Parse short date like 'Aug 28'
            from datetime import datetime
            dt = datetime.fromisoformat(last_event_time_utc.rstrip("Z"))
            date_str = dt.strftime("%b %d")
        except Exception:
            pass

    if date_str:
        reasons.append(f"latest {date_str}")
        
    reason_text = " · ".join(reasons) if reasons else "Recent weather activity"
    return label, reason_text
