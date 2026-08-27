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
    from app.services.ai_service import GeminiClient

    context = _build_context_block(job, storm_events)
    user_prompt = f"Data:\n{context}\n\nWrite the 2-3 sentence sales summary."

    try:
        client = GeminiClient()
        result = await client.generate_text(
            system_prompt=_SUMMARY_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return result.strip()
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
    from app.services.ai_service import GeminiClient

    context = _build_context_block(job, storm_events)
    user_prompt = f"Data:\n{context}\n\nWrite the door-knocking opening script."

    try:
        client = GeminiClient()
        result = await client.generate_text(
            system_prompt=_DOOR_SCRIPT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        return result.strip()
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
