"""
US Census Bureau Open Data Enrichment Service.

Fetches median household income (ACS-5 table B19013_001E) and median year
structure built (ACS-5 table B25035_001E) via public US Census Geocoder & ACS API.
Zero paid API keys required. Cached locally in SQLite WAL to eliminate redundant requests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timezone
from typing import Any, cast

import httpx
import structlog

from app.core.database import get_connection

logger = structlog.get_logger("app.services.census_enrichment")

CENSUS_TIMEOUT_SECONDS = 3.0
GEOCODER_BASE_URL = "https://geocoding.geo.census.gov/geocoder/geographies/coordinates"
ACS_BASE_URL = "https://api.census.gov/data/2022/acs/acs5"


def _init_census_cache_table() -> None:
    """Ensure the local census enrichment SQLite cache table exists."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS census_enrichment_cache (
                lat_lon_key TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception as exc:
        logger.warning("census_cache_init_failed", error=str(exc))
    finally:
        conn.close()


def _get_cache_key(lat: float, lon: float) -> str:
    """Format coordinates to 3 decimal places (~100m grid) for spatial caching."""
    return f"{round(lat, 3):.3f},{round(lon, 3):.3f}"


def _get_cached_census_data(cache_key: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        cur = conn.execute("SELECT data_json FROM census_enrichment_cache WHERE lat_lon_key = ?", (cache_key,))
        row = cur.fetchone()
        if row and row["data_json"]:
            return cast(dict[str, Any], json.loads(row["data_json"]))
        return None
    except Exception:
        return None
    finally:
        conn.close()


def _set_cached_census_data(cache_key: str, data: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO census_enrichment_cache (lat_lon_key, data_json) VALUES (?, ?)",
            (cache_key, json.dumps(data)),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("set_census_cache_failed", error=str(exc))
    finally:
        conn.close()


async def get_census_enrichment(
    latitude: float | None,
    longitude: float | None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """
    Fetch and return US Census ACS-5 enrichment data for given latitude/longitude.

    Returns:
        dict with keys:
          - median_household_income (int | None)
          - median_year_built (int | None)
          - estimated_home_age (int | None)
          - income_badge (str | None, e.g. "Median Income: $78k")
          - home_age_badge (str | None, e.g. "Est. Home Age: 24 yrs")
        Or None if coordinates are missing or external requests fail.
    """
    if latitude is None or longitude is None:
        return None

    _init_census_cache_table()
    cache_key = _get_cache_key(latitude, longitude)
    cached = _get_cached_census_data(cache_key)
    if cached is not None:
        return cached

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=CENSUS_TIMEOUT_SECONDS)
        close_client = True

    try:
        # Step 1: Query Census Geocoder for FIPS codes
        geo_params: dict[str, str | float] = {
            "x": longitude,
            "y": latitude,
            "benchmark": "Public_AR_Current",
            "vintage": "Current_Current",
            "format": "json",
        }
        geo_resp = await client.get(GEOCODER_BASE_URL, params=geo_params)
        if geo_resp.status_code != 200:
            logger.debug("census_geocoder_non_200", status=geo_resp.status_code)
            return None

        geo_data = geo_resp.json()
        geographies = geo_data.get("result", {}).get("geographies", {})
        tracts = geographies.get("Census Tracts", [])
        if not tracts:
            logger.debug("census_no_tracts_found", lat=latitude, lon=longitude)
            return None

        tract_info = tracts[0]
        state_fips = tract_info.get("STATE")
        county_fips = tract_info.get("COUNTY")
        tract_fips = tract_info.get("TRACT")

        if not (state_fips and county_fips and tract_fips):
            return None

        # Step 2: Query ACS-5 API for income and structure age
        acs_params = {
            "get": "NAME,B19013_001E,B25035_001E",
            "for": f"tract:{tract_fips}",
            "in": f"state:{state_fips}+county:{county_fips}",
        }
        acs_resp = await client.get(ACS_BASE_URL, params=acs_params)
        if acs_resp.status_code != 200:
            logger.debug("census_acs_non_200", status=acs_resp.status_code)
            return None

        acs_rows = acs_resp.json()
        if len(acs_rows) < 2:
            return None

        header = acs_rows[0]
        values = acs_rows[1]
        data_dict = dict(zip(header, values))

        # Parse income
        raw_income = data_dict.get("B19013_001E")
        income: int | None = None
        if raw_income is not None:
            try:
                val = int(raw_income)
                if val > 0:
                    income = val
            except (ValueError, TypeError):
                pass

        # Parse year built
        raw_year = data_dict.get("B25035_001E")
        year_built: int | None = None
        if raw_year is not None:
            try:
                val = int(raw_year)
                if 1800 <= val <= 2030:
                    year_built = val
            except (ValueError, TypeError):
                pass

        current_year = datetime.now(UTC).year
        home_age = (current_year - year_built) if year_built else None

        result: dict[str, Any] = {
            "median_household_income": income,
            "median_year_built": year_built,
            "estimated_home_age": home_age,
            "income_badge": f"Median Income: ${income // 1000}k" if income else None,
            "home_age_badge": f"Est. Home Age: {home_age} yrs" if home_age else None,
            "census_tract": f"{state_fips}{county_fips}{tract_fips}",
        }

        _set_cached_census_data(cache_key, result)
        logger.info("census_enrichment_success", lat=latitude, lon=longitude, income=income, home_age=home_age)
        return result

    except Exception as exc:
        logger.warning("census_enrichment_error", lat=latitude, lon=longitude, error=str(exc))
        return None
    finally:
        if close_client:
            await client.aclose()
