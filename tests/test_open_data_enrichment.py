"""
Unit Tests for Open Data Canvassing Enrichment (Sprint 6).

Covers:
1. US Census Bureau ACS-5 income and structure age enrichment + caching + error fallbacks.
2. OpenStreetMap Overpass building footprint polygon area calculation + roof square estimation + caching + error fallbacks.
3. Canvassing targets integration and API route verification.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.main import app
from app.services.canvassing_targets import (
    enrich_target,
    get_enriched_canvassing_targets,
    get_ranked_canvassing_targets,
)
from app.services.census_enrichment import (
    _get_cache_key as get_census_cache_key,
    get_census_enrichment,
)
from app.services.osm_footprint import (
    _get_cache_key as get_osm_cache_key,
    calculate_polygon_area_sqm,
    get_osm_building_footprint,
)

client = TestClient(app)

login_resp = client.post("/auth/login", data={"pin": "2222", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = login_resp.cookies.get("auth_token")
if auth_cookie:
    client.cookies.set("auth_token", auth_cookie)


# ── 1. Census Enrichment Tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_census_enrichment_success():
    lat, lon = 30.8327, -83.2785
    mock_client = AsyncMock()

    # Geocoder response mock
    geo_resp = MagicMock()
    geo_resp.status_code = 200
    geo_resp.json.return_value = {
        "result": {
            "geographies": {
                "Census Tracts": [
                    {"STATE": "13", "COUNTY": "185", "TRACT": "010100"}
                ]
            }
        }
    }

    # ACS-5 response mock
    acs_resp = MagicMock()
    acs_resp.status_code = 200
    acs_resp.json.return_value = [
        ["NAME", "B19013_001E", "B25035_001E", "state", "county", "tract"],
        ["Census Tract 101, Lowndes County, Georgia", "65400", "2002", "13", "185", "010100"],
    ]

    mock_client.get.side_effect = [geo_resp, acs_resp]

    result = await get_census_enrichment(lat, lon, client=mock_client)

    assert result is not None
    assert result["median_household_income"] == 65400
    assert result["median_year_built"] == 2002
    assert result["estimated_home_age"] is not None
    assert result["income_badge"] == "Median Income: $65k"
    assert "Est. Home Age:" in result["home_age_badge"]
    assert result["census_tract"] == "13185010100"

    # Verify cached in SQLite
    cache_key = get_census_cache_key(lat, lon)
    cached = await get_census_enrichment(lat, lon, client=None)
    assert cached is not None
    assert cached["median_household_income"] == 65400


@pytest.mark.asyncio
async def test_census_enrichment_missing_coords():
    assert await get_census_enrichment(None, -83.2785) is None
    assert await get_census_enrichment(30.8327, None) is None


@pytest.mark.asyncio
async def test_census_enrichment_geocoder_non_200():
    mock_client = AsyncMock()
    geo_resp = MagicMock()
    geo_resp.status_code = 404
    mock_client.get.return_value = geo_resp

    result = await get_census_enrichment(31.0, -84.0, client=mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_census_enrichment_timeout_fallback():
    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.TimeoutException("Census API timed out")

    result = await get_census_enrichment(31.0, -84.0, client=mock_client)
    assert result is None


# ── 2. OSM Footprint Tests ──────────────────────────────────────────────────────


def test_polygon_area_calculation():
    # 4-point rectangle near Valdosta, GA
    # Approx 10m x 20m box = 200 sqm
    coords = [
        (30.832700, -83.278500),
        (30.832790, -83.278500),
        (30.832790, -83.278710),
        (30.832700, -83.278710),
    ]
    area = calculate_polygon_area_sqm(coords)
    assert 150.0 < area < 250.0

    # Under 3 points returns 0
    assert calculate_polygon_area_sqm([(30.0, -83.0)]) == 0.0


@pytest.mark.asyncio
async def test_osm_footprint_success():
    lat, lon = 30.8400, -83.2800
    mock_client = AsyncMock()

    overpass_resp = MagicMock()
    overpass_resp.status_code = 200
    overpass_resp.json.return_value = {
        "elements": [
            {
                "type": "way",
                "id": 123456,
                "geometry": [
                    {"lat": 30.840000, "lon": -83.280000},
                    {"lat": 30.840135, "lon": -83.280000},
                    {"lat": 30.840135, "lon": -83.280150},
                    {"lat": 30.840000, "lon": -83.280150},
                ],
            }
        ]
    }
    mock_client.post.return_value = overpass_resp

    result = await get_osm_building_footprint(lat, lon, client=mock_client)

    assert result is not None
    assert result["footprint_sqm"] > 0
    assert result["footprint_sqft"] > 0
    assert result["estimated_squares"] > 0
    assert "Est. Roof:" in result["roof_squares_badge"]
    assert "(OSM)" in result["roof_squares_badge"]

    # Verify cached in SQLite
    cached = await get_osm_building_footprint(lat, lon, client=None)
    assert cached is not None
    assert cached["footprint_sqft"] == result["footprint_sqft"]


@pytest.mark.asyncio
async def test_osm_footprint_empty_elements():
    mock_client = AsyncMock()
    overpass_resp = MagicMock()
    overpass_resp.status_code = 200
    overpass_resp.json.return_value = {"elements": []}
    mock_client.post.return_value = overpass_resp

    result = await get_osm_building_footprint(31.5, -84.5, client=mock_client)
    assert result is None


@pytest.mark.asyncio
async def test_osm_footprint_timeout_fallback():
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.TimeoutException("OSM Overpass timed out")

    result = await get_osm_building_footprint(31.5, -84.5, client=mock_client)
    assert result is None


# ── 3. Canvassing Target Enrichment Integration Tests ──────────────────────────


@pytest.mark.asyncio
async def test_enrich_target_integration():
    target = {
        "location": "Valdosta, GA",
        "zipcode": "31602",
        "event_count": 5,
        "max_severity_score": 8.5,
        "latitude": 30.8327,
        "longitude": -83.2785,
    }

    mock_census = {
        "median_household_income": 72000,
        "median_year_built": 2005,
        "estimated_home_age": 21,
        "income_badge": "Median Income: $72k",
        "home_age_badge": "Est. Home Age: 21 yrs",
    }
    mock_osm = {
        "footprint_sqft": 2400.0,
        "estimated_squares": 27.6,
        "roof_squares_badge": "Est. Roof: 28 SQ (OSM)",
    }

    with patch("app.services.canvassing_targets.get_census_enrichment", AsyncMock(return_value=mock_census)), \
         patch("app.services.canvassing_targets.get_osm_building_footprint", AsyncMock(return_value=mock_osm)):

        enriched = await enrich_target(target)

        assert "enrichment" in enriched
        assert enriched["enrichment"]["census"] == mock_census
        assert enriched["enrichment"]["osm"] == mock_osm
        assert "Median Income: $72k" in enriched["enrichment"]["badges"]
        assert "Est. Home Age: 21 yrs" in enriched["enrichment"]["badges"]
        assert "Est. Roof: 28 SQ (OSM)" in enriched["enrichment"]["badges"]
        # Original keys remain untouched
        assert enriched["max_severity_score"] == 8.5


@pytest.mark.asyncio
async def test_enrich_target_graceful_fallback():
    target = {
        "location": "Thomasville, GA",
        "zipcode": "31792",
        "latitude": None,
        "longitude": None,
    }

    enriched = await enrich_target(target)
    assert "enrichment" in enriched
    assert enriched["enrichment"]["census"] is None
    assert enriched["enrichment"]["osm"] is None
    assert enriched["enrichment"]["badges"] == []


def test_office_storm_targets_endpoint_with_enrichment():
    # Insert a dummy storm event to ensure targets exist
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO storm_events (
                id, event_type, event_date, county, zipcode, severity_score, hail_size_inches,
                wind_speed_mph, report_time_utc, ingested_at, distance_miles_from_office,
                latitude, longitude
            ) VALUES (
                'test_storm_enrich_1', 'HAIL', CURRENT_TIMESTAMP, 'Lowndes', '31602', 7.8, 1.75,
                0.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 5.0, 30.8327, -83.2785
            )
        """)
        conn.commit()
    finally:
        conn.close()

    mock_census = {
        "median_household_income": 68000,
        "income_badge": "Median Income: $68k",
        "home_age_badge": "Est. Home Age: 15 yrs",
    }
    mock_osm = {
        "footprint_sqft": 2200.0,
        "estimated_squares": 25.3,
        "roof_squares_badge": "Est. Roof: 25 SQ (OSM)",
    }

    with patch("app.services.canvassing_targets.get_census_enrichment", AsyncMock(return_value=mock_census)), \
         patch("app.services.canvassing_targets.get_osm_building_footprint", AsyncMock(return_value=mock_osm)):

        response = client.get(
            "/api/office/storms/targets?enrich=true",
            headers={"x-internal-token": auth_cookie} if auth_cookie else {},
        )
        assert response.status_code == 200
        data = response.json()
        assert "targets" in data
        if len(data["targets"]) > 0:
            first = data["targets"][0]
            assert "enrichment" in first
            assert "Median Income: $68k" in first["enrichment"]["badges"]
