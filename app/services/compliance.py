"""
Georgia Legal & Statutory Compliance Service.

Enforces:
1. Georgia Fair Business Practices Act (O.C.G.A. § 10-1-393.12):
   - 5-business-day post-denial cancellation period before invoice generation.
   - Detachable Notice of Cancellation and 10pt bold disclosure requirements.
2. Georgia SB 201 (O.C.G.A. § 33-24-59.28, effective July 1, 2025):
   - Prohibition of Assignment of Benefits (AOB) language in post-disaster / insurance contracts.
3. 7-Year Statutory Document Retention & Soft-Delete Standards.
"""

from __future__ import annotations

import datetime
import json
import re
from datetime import timezone
from typing import Any

import structlog
from fastapi import HTTPException

from app.core.database import get_connection

logger = structlog.get_logger("app.services.compliance")

# AOB prohibited patterns under Georgia SB 201 (O.C.G.A. § 33-24-59.28)
AOB_PATTERNS = [
    r"\bassign(?:ment|s|ed|ing)?\s+(?:of\s+)?(?:all\s+)?(?:insurance\s+)?benefits\b",
    r"\bassign(?:ment|s|ed|ing)?\s+(?:to\s+contractor\s+)?(?:all\s+)?(?:insurance\s+)?(?:proceeds|rights|claims|policy\s+rights)\b",
    r"\bauthorize\s+(?:direct\s+)?payment\s+of\s+insurance\s+proceeds\s+(?:directly\s+)?to\s+(?:contractor|wickham)\b",
    r"\bdirect\s+payment\s+of\s+insurance\s+(?:proceeds|benefits)\s+to\s+contractor\b",
    r"\btransfer(?:s|red|ring)?\s+(?:all\s+)?insurance\s+rights\b",
    r"\bpower\s+of\s+attorney\s+for\s+insurance\s+proceeds\b",
]

_AOB_REGEXES = [re.compile(p, re.IGNORECASE) for p in AOB_PATTERNS]


def detect_aob_language(text: str | None) -> list[str]:
    """
    Scan contract scope/description text or custom clauses for prohibited
    Assignment of Benefits (AOB) language under Georgia SB 201.
    
    Returns a list of matched phrases found in the text.
    """
    if not text:
        return []
    
    matches: list[str] = []
    for pattern in _AOB_REGEXES:
        found = pattern.findall(text)
        if found:
            matches.extend(found if isinstance(found, list) else [found])
    return matches


def validate_no_aob_language(text: str | None, is_insurance_job: bool = True) -> None:
    """
    Validate that text contains no AOB language. If detected on an insurance job,
    raises HTTP 400 citing Georgia SB 201.
    """
    if not is_insurance_job or not text:
        return
    
    detected = detect_aob_language(text)
    if detected:
        logger.warning("aob_language_detected", detected=detected)
        raise HTTPException(
            status_code=400,
            detail=(
                "Assignment of Benefits (AOB) language detected in contract scope/terms: "
                f"'{', '.join(set(detected))}'. "
                "Under Georgia SB 201 (O.C.G.A. § 33-24-59.28), post-disaster residential roofing "
                "contracts cannot assign insurance benefits or rights to a contractor. "
                "Please remove or revise this clause before proceeding."
            ),
        )


def calculate_business_days_deadline(start_dt: datetime.datetime, business_days: int = 5) -> datetime.datetime:
    """
    Compute a deadline adding `business_days` (skipping Saturdays and Sundays)
    from `start_dt`.
    """
    current = start_dt
    added = 0
    while added < business_days:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            added += 1
    return current


def is_post_denial_invoicing_locked(
    job_id: str,
    is_emergency: bool = False,
    current_time: datetime.datetime | None = None,
) -> tuple[bool, str | None, datetime.datetime | None]:
    """
    Deterministic guard enforcing O.C.G.A. § 10-1-393.12:
    Prevents invoice creation or non-emergency collection on insurance-contingent
    jobs until 5 business days have elapsed since a CLAIM_DENIED status was recorded.

    Emergency services (e.g. temporary tarping) are exempt under statute.
    Retail/non-insurance jobs are exempt.

    Returns:
        (is_locked: bool, reason_message: str | None, unlock_datetime: datetime | None)
    """
    if is_emergency:
        return False, None, None

    now = current_time or datetime.datetime.now(datetime.UTC)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, status, job_type, status_history FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return False, None, None

        job_type = (row["job_type"] or "insurance").lower()
        if job_type == "retail":
            return False, None, None

        # Check status history for the most recent CLAIM_DENIED transition
        status_history_raw = row["status_history"]
        if not status_history_raw:
            return False, None, None

        try:
            history = json.loads(status_history_raw) if isinstance(status_history_raw, str) else status_history_raw
        except Exception:
            return False, None, None

        denial_timestamp: datetime.datetime | None = None
        for entry in reversed(history):
            if isinstance(entry, dict) and entry.get("status") == "CLAIM_DENIED":
                ts_str = entry.get("timestamp")
                if ts_str:
                    try:
                        # Parse ISO format timestamp
                        ts_clean = ts_str.rstrip("Z")
                        if "+" in ts_clean:
                            denial_timestamp = datetime.datetime.fromisoformat(ts_str)
                        else:
                            denial_timestamp = datetime.datetime.fromisoformat(ts_clean).replace(tzinfo=datetime.UTC)
                        break
                    except Exception:
                        pass

        if not denial_timestamp:
            return False, None, None

        # Ensure denial_timestamp is timezone-aware
        if denial_timestamp.tzinfo is None:
            denial_timestamp = denial_timestamp.replace(tzinfo=datetime.UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.UTC)

        unlock_dt = calculate_business_days_deadline(denial_timestamp, business_days=5)

        if now < unlock_dt:
            date_str = unlock_dt.strftime("%Y-%m-%d %H:%M UTC")
            msg = (
                f"Invoicing locked: 5-business-day cancellation period active until {date_str} "
                "under Georgia Fair Business Practices Act (O.C.G.A. § 10-1-393.12). Emergency services are exempt."
            )
            return True, msg, unlock_dt

        return False, None, None
    finally:
        conn.close()
