"""
AI Sales Narrative Service.

Generates grounded, factual sales summaries and door-knocking scripts
using the existing GeminiClient. All prompts are constructed from real
job and storm data stored in SQLite — no fabricated details are allowed.
"""
from __future__ import annotations

import structlog

logger = structlog.get_logger("app.services.sales_narrative")

_SUMMARY_SYSTEM_PROMPT = (
    "You are a roofing sales assistant for Wickham Roofing & Restoration. "
    "Write a short (2–3 sentence) factual sales summary for a field representative "
    "to use when speaking with a homeowner. "
    "Use ONLY the data provided — do not invent storm dates, hail sizes, or addresses. "
    "Be professional, empathetic, and action-oriented. Do not include disclaimers."
)

_DOOR_SCRIPT_SYSTEM_PROMPT = (
    "You are a roofing sales coach for Wickham Roofing & Restoration. "
    "Write a short, friendly door-knocking opening script (4–6 sentences) for a field rep. "
    "Use ONLY the data provided — do not invent any storm, damage, or homeowner details. "
    "The script should introduce the rep, reference the specific storm event if provided, "
    "and invite the homeowner to schedule a FREE inspection. "
    "Do not include bracketed placeholders like [Name] — use the actual values from the data."
)


def _build_context_block(job: dict, storm_events: list[dict]) -> str:
    """Build a plain-text data block that anchors the AI prompt to real facts."""
    lines = [
        f"Homeowner address: {job.get('address_line1', '')}, {job.get('city', '')}, {job.get('state', '')} {job.get('postal_code', '')}",
        f"Job status: {job.get('status', 'LEAD_CAPTURED')}",
    ]
    if job.get("loss_date"):
        lines.append(f"Reported loss date: {job['loss_date']}")
    if job.get("insurer_name"):
        lines.append(f"Insurance carrier: {job['insurer_name']}")

    if storm_events:
        lines.append("\nNearby storm events:")
        for ev in storm_events[:3]:  # cap at 3 to keep prompt tight
            etype = ev.get("event_type", "UNKNOWN")
            location = ev.get("county") or ev.get("location") or "nearby area"
            ts = ev.get("report_time_utc") or ev.get("last_event_utc") or ""
            hail = ev.get("hail_size_inches") or ev.get("max_hail_inches")
            wind = ev.get("wind_speed_mph") or ev.get("max_wind_mph")
            detail_parts = []
            if hail:
                detail_parts.append(f"{hail}\" hail")
            if wind:
                detail_parts.append(f"{wind} mph wind")
            detail = ", ".join(detail_parts) if detail_parts else etype
            lines.append(f"  - {etype} event in {location} ({ts[:10] if ts else 'recent'}): {detail}")
    else:
        lines.append("\nNo specific storm events on record for this area yet.")

    return "\n".join(lines)


async def generate_sales_summary(job: dict, storm_events: list[dict]) -> str:
    """
    Generate a 2–3 sentence sales summary grounded in the provided job and storm data.

    Args:
        job: Job dict from database (address, status, loss_date, etc.).
        storm_events: List of storm event dicts from get_storm_target_summaries()
                      or nearby events for the job's ZIP code.

    Returns:
        Plain-text summary string.
    """
    from app.services.ai import GeminiClient, verify_legal_disclaimers
    from app.services.ai.guardrails import verify_prohibited_sales_promises

    context = _build_context_block(job, storm_events)
    user_prompt = f"Data:\n{context}\n\nWrite the 2-3 sentence sales summary."

    try:
        client = GeminiClient()
        result = await client.generate_text(
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        text = str(result).strip()

        # Enforce negative guardrail (no coverage/rebate promises)
        prohibited = verify_prohibited_sales_promises(text)
        if not prohibited.passed:
            logger.warning("prohibited_sales_language_intercepted", violations=prohibited.violations)
            addr = job.get("address_line1", "this property")
            return (
                f"Recent storm activity was recorded near {addr}. "
                "A free, non-obligation roof inspection from Wickham Roofing can document visible conditions "
                "to determine whether an insurance claim should be considered."
            )

        # Enforce legal disclaimers guardrail
        check = verify_legal_disclaimers(text, disclaimer_type="sales_script")
        if not check.passed:
            text += " A free inspection from Wickham Roofing involves no obligation."
        return str(text)
    except Exception as exc:
        logger.error("sales_summary_generation_failed", error=str(exc))
        # Graceful fallback — do not raise, return a generic message
        addr = job.get("address_line1", "this property")
        return (
            f"Recent storm activity has been reported near {addr}. "
            "This home may have sustained roof damage. "
            "A free inspection from Wickham Roofing can determine if a claim is warranted."
        )


async def generate_door_script(job: dict, storm_events: list[dict]) -> str:
    """
    Generate a short, personalized door-knocking script grounded in real data.

    Args:
        job: Job dict from database.
        storm_events: List of nearby storm event dicts.

    Returns:
        Plain-text door script string.
    """
    from app.services.ai import GeminiClient, verify_legal_disclaimers
    from app.services.ai.guardrails import verify_prohibited_sales_promises

    context = _build_context_block(job, storm_events)
    user_prompt = f"Data:\n{context}\n\nWrite the door-knocking opening script."

    try:
        client = GeminiClient()
        result = await client.generate_text(
            system_prompt=_DOOR_SCRIPT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        text = str(result).strip()

        # Enforce negative guardrail
        prohibited = verify_prohibited_sales_promises(text)
        if not prohibited.passed:
            logger.warning("prohibited_door_script_language_intercepted", violations=prohibited.violations)
            addr = job.get("address_line1", "your neighborhood")
            return (
                f"Hi, I'm with Wickham Roofing & Restoration. "
                f"We've been in the area near {addr} following recent verified storm reports. "
                "We're offering complimentary roof inspections to check for any visible weather impact. "
                "Would you have 15 minutes for us to take a quick look? "
                "There's no obligation — we just want to make sure your home is secure."
            )

        # Enforce legal disclaimers guardrail
        check = verify_legal_disclaimers(text, disclaimer_type="sales_script")
        if not check.passed:
            text += " There's no obligation — we just want to make sure your home is protected."
        return str(text)
    except Exception as exc:
        logger.error("door_script_generation_failed", error=str(exc))
        addr = job.get("address_line1", "your neighborhood")
        return (
            f"Hi, I'm with Wickham Roofing & Restoration. "
            f"We've been in the area near {addr} following recent storm reports. "
            "We're offering free roof inspections to homeowners who may have sustained damage. "
            "Would you have 15 minutes for us to take a quick look? "
            "There's no obligation — we just want to make sure your home is protected."
        )


def build_sales_provenance(job: dict, storm_events: list[dict]) -> dict:
    """
    Return explicit citations and grounding sources for sales tools.
    """
    cited_storms = []
    for ev in storm_events[:3]:
        etype = ev.get("event_type", "STORM")
        hail = ev.get("hail_size_inches") or ev.get("max_hail_inches")
        wind = ev.get("wind_speed_mph") or ev.get("max_wind_mph")
        loc = ev.get("county") or ev.get("location") or "Local area"
        ts = ev.get("report_time_utc") or ev.get("last_event_utc") or ""
        cited_storms.append({
            "event_type": etype,
            "location": loc,
            "timestamp": ts[:10] if ts else "Recent",
            "magnitude": f"{hail}\" hail" if hail else (f"{wind} mph wind" if wind else "Verified event"),
        })

    return {
        "grounded_job_address": f"{job.get('address_line1', '')}, {job.get('city', '')} {job.get('state', '')}".strip(" ,"),
        "job_status": job.get("status", "LEAD_CAPTURED"),
        "storm_events_cited": cited_storms,
        "citation_count": len(cited_storms),
        "disclaimer": "AI-assisted draft — verify before use. Does not constitute an insurance coverage or payment guarantee.",
    }
