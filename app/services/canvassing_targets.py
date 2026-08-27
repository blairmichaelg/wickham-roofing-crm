"""
Canvassing Targets Service.

Thin wrapper over the database helper that provides a clean API surface
for the `/api/storms/targets` endpoint and dashboard widgets.
"""
from __future__ import annotations

from app.core.database import get_storm_target_summaries


def get_ranked_canvassing_targets(
    window_hours: int = 72,
    limit: int = 10,
    min_hail: float | None = None,
    min_wind: float | None = None,
) -> list[dict]:
    """
    Return a ranked list of canvassing target areas based on recent storm activity.

    Each item in the returned list includes:
      - location       : county / location string (e.g. "Thomasville, GA")
      - zipcode        : ZIP code nearest to event centroid
      - event_count    : total qualifying events in the window
      - max_severity_score : highest computed severity score (0–10 scale)
      - max_hail_inches    : largest hail size reported (inches)
      - max_wind_mph       : highest wind gust reported (mph)
      - has_tornado        : True if any tornado event is included
      - last_event_utc     : ISO-8601 timestamp of the most recent event
      - event_types        : comma-separated list of distinct event types

    Args:
        window_hours: How far back to look (default 72 hours).
        limit: Maximum number of target areas to return (default 10).
        min_hail: Minimum hail size threshold in inches (uses config default if None).
        min_wind: Minimum wind speed threshold in mph (uses config default if None).

    Returns:
        List of dicts, sorted descending by max_severity_score.
    """
    return get_storm_target_summaries(
        window_hours=window_hours,
        limit=limit,
        min_hail=min_hail,
        min_wind=min_wind,
    )
