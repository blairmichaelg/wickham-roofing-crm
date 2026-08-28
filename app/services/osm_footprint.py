"""
OpenStreetMap Overpass Building Footprint Service.

Fetches building footprints within ~50m of given coordinates via public OSM Overpass API.
Calculates polygon footprint area (sq ft) and estimates roof squares (using 1.15 pitch factor)
prior to ordering aerial measurement reports.
Zero paid API keys required. Cached locally in SQLite WAL.
"""

from __future__ import annotations

import json
import math
from typing import Any, cast

import httpx
import structlog

from app.core.database import get_connection

logger = structlog.get_logger("app.services.osm_footprint")

OSM_TIMEOUT_SECONDS = 3.0
OVERPASS_API_URL = "https://overpass-api.de/api/interpreter"
STANDARD_PITCH_FACTOR = 1.15  # standard multiplier for gable/hip roof slopes
METERS_TO_SQFT = 10.7639
EARTH_RADIUS_METERS = 6378137.0


def _init_osm_cache_table() -> None:
    """Ensure the local OSM footprint SQLite cache table exists."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS osm_footprint_cache (
                lat_lon_key TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception as exc:
        logger.warning("osm_cache_init_failed", error=str(exc))
    finally:
        conn.close()


def _get_cache_key(lat: float, lon: float) -> str:
    """Format coordinates to 4 decimal places (~10m precision) for footprint caching."""
    return f"{round(lat, 4):.4f},{round(lon, 4):.4f}"


def _get_cached_osm_data(cache_key: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        cur = conn.execute("SELECT data_json FROM osm_footprint_cache WHERE lat_lon_key = ?", (cache_key,))
        row = cur.fetchone()
        if row and row["data_json"]:
            return cast(dict[str, Any], json.loads(row["data_json"]))
        return None
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached_osm_data(cache_key: str, data: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO osm_footprint_cache (lat_lon_key, data_json) VALUES (?, ?)",
            (cache_key, json.dumps(data)),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("set_osm_cache_failed", error=str(exc))
    finally:
        conn.close()


def calculate_polygon_area_sqm(coords: list[tuple[float, float]]) -> float:
    """
    Calculate planar polygon area in square meters from a list of (lat, lon) pairs
    using the Shoelace formula on local equirectangular projected coordinates.
    """
    if len(coords) < 3:
        return 0.0

    lat0 = coords[0][0]
    lat0_rad = math.radians(lat0)

    # Convert lat/lon degrees to meters relative to first vertex
    points_m: list[tuple[float, float]] = []
    for lat, lon in coords:
        x = math.radians(lon - coords[0][1]) * EARTH_RADIUS_METERS * math.cos(lat0_rad)
        y = math.radians(lat - coords[0][0]) * EARTH_RADIUS_METERS
        points_m.append((x, y))

    # Shoelace formula
    area = 0.0
    n = len(points_m)
    for i in range(n):
        j = (i + 1) % n
        area += points_m[i][0] * points_m[j][1]
        area -= points_m[j][0] * points_m[i][1]

    return abs(area) / 2.0


async def get_osm_building_footprint(
    latitude: float | None,
    longitude: float | None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """
    Fetch building footprint geometry from OSM Overpass API and estimate roof squares.

    Returns:
        dict with keys:
          - footprint_sqm (float)
          - footprint_sqft (float)
          - estimated_squares (float)
          - roof_squares_badge (str, e.g. "Est. Roof: 32 SQ (OSM)")
        Or None if coordinates are missing, no buildings found, or API request fails.
    """
    if latitude is None or longitude is None:
        return None

    _init_osm_cache_table()
    cache_key = _get_cache_key(latitude, longitude)
    cached = _get_cached_osm_data(cache_key)
    if cached is not None:
        return cached

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=OSM_TIMEOUT_SECONDS)
        close_client = True

    try:
        # Overpass QL query: find building ways/relations within 50m of centroid
        query = f"""
        [out:json][timeout:3];
        (
          way["building"](around:50,{latitude},{longitude});
          relation["building"](around:50,{latitude},{longitude});
        );
        out geom;
        """
        response = await client.post(OVERPASS_API_URL, data={"data": query})
        if response.status_code != 200:
            logger.debug("osm_overpass_non_200", status=response.status_code)
            return None

        data = response.json()
        elements = data.get("elements", [])
        if not elements:
            logger.debug("osm_no_buildings_in_radius", lat=latitude, lon=longitude)
            return None

        # Extract polygons from ways
        max_area_sqm = 0.0
        for elem in elements:
            geometry = elem.get("geometry", [])
            if len(geometry) >= 3:
                coords = [(pt["lat"], pt["lon"]) for pt in geometry if "lat" in pt and "lon" in pt]
                poly_area = calculate_polygon_area_sqm(coords)
                if poly_area > max_area_sqm:
                    max_area_sqm = poly_area

        if max_area_sqm <= 0.0:
            return None

        footprint_sqft = round(max_area_sqm * METERS_TO_SQFT, 1)
        estimated_squares = round((footprint_sqft * STANDARD_PITCH_FACTOR) / 100.0, 1)

        result: dict[str, Any] = {
            "footprint_sqm": round(max_area_sqm, 2),
            "footprint_sqft": footprint_sqft,
            "estimated_squares": estimated_squares,
            "roof_squares_badge": f"Est. Roof: {int(round(estimated_squares))} SQ (OSM)",
        }

        _set_cached_osm_data(cache_key, result)
        logger.info("osm_footprint_success", lat=latitude, lon=longitude, sqft=footprint_sqft, squares=estimated_squares)
        return result

    except Exception as exc:
        logger.warning("osm_footprint_error", lat=latitude, lon=longitude, error=str(exc))
        return None
    finally:
        if close_client:
            await client.aclose()
