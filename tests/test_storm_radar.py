import sys
import json
import sqlite3
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from app.main import app
from app.config import get_settings
from app.core.database import get_connection
from app.services.storm_feed import NWSLiveStormFeed
from app.workers.storm_worker import ingest_storm_events

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_storm_events():
    """Clear the storm_events table before each test to ensure isolation."""
    conn = get_connection()
    conn.execute("DELETE FROM storm_events")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
@patch("app.services.storm_feed.httpx.AsyncClient.get")
async def test_nws_live_storm_feed_fetch_and_parse(mock_get):
    """Test NWSLiveStormFeed fetches and parses correctly from NWS/Nominatim."""
    # 1. Mock ArcGIS REST response
    mock_arcgis_resp = MagicMock()
    mock_arcgis_resp.status_code = 200
    mock_arcgis_resp.json.return_value = {
        "features": [
            {
                "attributes": {
                    "objectid": 1001,
                    "descript": "HAIL",
                    "magnitude": "1.75",
                    "valid_time": "2026-08-16 14:30:00+00",
                    "remarks": "Golf ball size hail reported",
                    "loc_desc": "Valdosta",
                    "state": "GA"
                },
                "geometry": {
                    "y": 30.8327,
                    "x": -83.2785
                }
            }
        ]
    }
    
    # 2. Mock Nominatim reverse geocode response
    mock_nominatim_resp = MagicMock()
    mock_nominatim_resp.status_code = 200
    mock_nominatim_resp.json.return_value = {
        "address": {
            "county": "Lowndes County"
        }
    }
    
    mock_get.side_effect = [mock_arcgis_resp, mock_nominatim_resp]
    
    feed = NWSLiveStormFeed()
    reports = await feed.fetch_recent_reports()
    
    assert len(reports) == 1
    r = reports[0]
    assert r["id"] == "1001"
    assert r["event_type"] == "HAIL"
    assert r["hail_size_inches"] == 1.75
    assert r["county"] == "Lowndes County"
    assert r["latitude"] == 30.8327
    assert r["longitude"] == -83.2785


@pytest.mark.asyncio
@patch("app.workers.storm_worker.NWSLiveStormFeed.fetch_recent_reports")
async def test_ingest_storm_events_task(mock_fetch):
    """Test the ingest_storm_events background task processing, filtering, and alerts."""
    # Mock NWS reports returned
    mock_fetch.return_value = [
        {
            "id": "ALERT_001",
            "event_type": "HAIL",
            "hail_size_inches": 1.5,
            "wind_speed_mph": 0,
            "latitude": 30.85, # Near office (30.8766, -84.1994), distance ~20 miles
            "longitude": -84.00,
            "county": "Thomas County",
            "report_time_utc": "2026-08-16T14:30:00Z",
            "loc_desc": "Thomasville",
            "remarks": "Severe hail"
        },
        {
            "id": "ALERT_002",
            "event_type": "WIND",
            "hail_size_inches": 0,
            "wind_speed_mph": 30, # Too low magnitude for alert
            "latitude": 30.85,
            "longitude": -84.00,
            "county": "Thomas County",
            "report_time_utc": "2026-08-16T14:35:00Z",
            "loc_desc": "Thomasville",
            "remarks": "Light wind"
        },
        {
            "id": "ALERT_003",
            "event_type": "HAIL",
            "hail_size_inches": 2.0,
            "wind_speed_mph": 0,
            "latitude": 32.00, # Too far (>50 miles), should be ignored
            "longitude": -84.00,
            "county": "Dooly County",
            "report_time_utc": "2026-08-16T14:40:00Z",
            "loc_desc": "Vienna",
            "remarks": "Huge hail but far away"
        }
    ]
    
    # Mock Redis client for publishing
    mock_redis = AsyncMock()
    ctx = {"redis": mock_redis}
    
    # Mock open for zipcodes.json (isolate to app.workers.storm_worker)
    import io
    mock_zip_data = json.dumps({
        "31792": {"lat": 30.85, "lon": -84.00}
    })
    
    with patch("app.workers.storm_worker.open", return_value=io.StringIO(mock_zip_data), create=True):
        # Run the worker task
        await ingest_storm_events(ctx)
    
    # Verify DB contains only ALERT_001 and ALERT_002
    conn = get_connection()
    cursor = conn.execute("SELECT id, county, report_time_utc FROM storm_events ORDER BY id")
    rows = cursor.fetchall()
    conn.close()
    
    assert len(rows) == 2
    assert rows[0]["id"] == "ALERT_001"
    assert rows[0]["county"] == "Thomas County"
    assert rows[0]["report_time_utc"] == "2026-08-16T14:30:00Z"
    
    assert rows[1]["id"] == "ALERT_002"
    
    # Verify Redis publish was called exactly once (only ALERT_001 satisfies severity and proximity)
    assert mock_redis.publish.call_count == 1
    published_channel, published_msg = mock_redis.publish.call_args[0]
    assert published_channel == "channel:storm_alerts"
    
    alert_payload = json.loads(published_msg)
    assert alert_payload["event_type"] == "HAIL"
    assert alert_payload["hail_size_inches"] == 1.5
    assert alert_payload["county"] == "Thomas County"


def test_storm_rest_endpoints():
    """Test GET /api/storms/recent and GET /api/storms/summary REST endpoints."""
    # 1. Insert test data directly
    now_utc = datetime.now(timezone.utc)
    
    conn = get_connection()
    conn.execute(
        "INSERT INTO storm_events (id, zipcode, event_type, event_date, hail_size_inches, wind_speed_mph, latitude, longitude, county, report_time_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("TEST_H1", "31792", "HAIL", now_utc.strftime("%Y-%m-%d"), 1.5, 0, 30.8, -84.1, "Thomas County", now_utc.isoformat())
    )
    conn.execute(
        "INSERT INTO storm_events (id, zipcode, event_type, event_date, hail_size_inches, wind_speed_mph, latitude, longitude, county, report_time_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("TEST_W1", "31792", "WIND", (now_utc - timedelta(hours=12)).strftime("%Y-%m-%d"), 0, 65, 30.8, -84.1, "Thomas County", (now_utc - timedelta(hours=12)).isoformat())
    )
    conn.execute(
        "INSERT INTO storm_events (id, zipcode, event_type, event_date, hail_size_inches, wind_speed_mph, latitude, longitude, county, report_time_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("TEST_H2", "31792", "HAIL", (now_utc - timedelta(days=5)).strftime("%Y-%m-%d"), 2.0, 0, 30.8, -84.1, "Thomas County", (now_utc - timedelta(days=5)).isoformat()) # Older than 72 hours
    )
    conn.commit()
    conn.close()
    
    # 2. Login to get token for auth
    from app.api.auth import create_access_token
    token = create_access_token("admin")
    client.cookies.set("auth_token", token)
    
    # Test GET /api/storms/recent
    response = client.get("/api/storms/recent")
    assert response.status_code == 200
    recent_data = response.json()
    # Should only return TEST_H1 and TEST_W1 (within 72 hours)
    assert len(recent_data) == 2
    assert recent_data[0]["id"] == "TEST_H1"
    assert recent_data[1]["id"] == "TEST_W1"
    
    # Test GET /api/storms/summary
    response = client.get("/api/storms/summary")
    assert response.status_code == 200
    summary_data = response.json()
    
    assert "Thomas County" in summary_data
    thomas_stats = summary_data["Thomas County"]
    assert thomas_stats["hail_count"] == 1
    assert thomas_stats["wind_count"] == 1
    assert thomas_stats["max_hail_size"] == 1.5
    assert thomas_stats["max_wind_speed"] == 65


def test_field_websocket_auth():
    """Test GET/WebSocket /ws/field authentication rules."""
    from app.api.auth import create_access_token
    
    # Clear client cookies to avoid test pollution
    client.cookies.clear()
    
    # 1. Invalid token / missing token
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/field") as websocket:
            pass
            
    # 2. Authorized field user token
    field_token = create_access_token("field", rep_name="Matthew Zellers", rep_id="rep-123")
    with client.websocket_connect(f"/ws/field?token={field_token}") as websocket:
        pass
        
    # 3. Forbidden role token (e.g. accounting)
    accounting_token = create_access_token("accounting")
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/field?token={accounting_token}") as websocket:
            pass
