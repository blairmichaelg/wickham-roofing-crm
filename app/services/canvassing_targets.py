"""
Canvassing Targets Service.

Provides ranked canvassing target areas based on recent storm activity,
optionally enriched with US Census demographic data (median income, home age)
and OpenStreetMap building footprint roof size estimates.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from app.core.database import get_storm_target_summaries
from app.services.census_enrichment import get_census_enrichment
from app.services.osm_footprint import get_osm_building_footprint

logger = structlog.get_logger("app.services.canvassing_targets")


def get_ranked_canvassing_targets(
    window_hours: int = 72,
    limit: int = 10,
    min_hail: float | None = None,
    min_wind: float | None = None,
) -> list[dict[str, Any]]:
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
      - latitude / longitude : coordinates if available

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


async def enrich_target(
    target: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """
    Enrich a single canvassing target with Census and OSM data non-destructively.
    """
    lat = target.get("latitude")
    lon = target.get("longitude")

    gather_results: list[Any] = await asyncio.gather(
        get_census_enrichment(lat, lon, client=client),
        get_osm_building_footprint(lat, lon, client=client),
        return_exceptions=True,
    )

    census_res = gather_results[0]
    osm_res = gather_results[1]
    valid_census: dict[str, Any] | None = census_res if isinstance(census_res, dict) else None
    valid_osm: dict[str, Any] | None = osm_res if isinstance(osm_res, dict) else None

    badges: list[str] = []
    if valid_census:
        if valid_census.get("income_badge"):
            badges.append(valid_census["income_badge"])
        if valid_census.get("home_age_badge"):
            badges.append(valid_census["home_age_badge"])

    if valid_osm and valid_osm.get("roof_squares_badge"):
        badges.append(valid_osm["roof_squares_badge"])

    target["enrichment"] = {
        "census": valid_census,
        "osm": valid_osm,
        "badges": badges,
    }
    return target


async def get_enriched_canvassing_targets(
    window_hours: int = 72,
    limit: int = 10,
    min_hail: float | None = None,
    min_wind: float | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch ranked storm canvassing targets and asynchronously enrich with Census + OSM data.
    """
    targets = get_ranked_canvassing_targets(
        window_hours=window_hours,
        limit=limit,
        min_hail=min_hail,
        min_wind=min_wind,
    )
    if not targets:
        return []

    async with httpx.AsyncClient(timeout=3.0) as client:
        enriched_targets = await asyncio.gather(
            *(enrich_target(t, client=client) for t in targets),
            return_exceptions=False,
        )
    return list(enriched_targets)
