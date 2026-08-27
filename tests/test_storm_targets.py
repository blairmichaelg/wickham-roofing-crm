"""
Tests for storm canvassing target summaries and the /api/office/storms/targets endpoint.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_connection, get_storm_target_summaries
from app.main import app

from app.api.auth import create_access_token

client = TestClient(app)

# Generate valid JWT tokens for tests
admin_token = create_access_token("admin")
field_token = create_access_token("field")

ADMIN_HEADERS = {"x-internal-token": admin_token}
FIELD_HEADERS = {"x-internal-token": field_token}


def _insert_storm(
    event_type: str = "HAIL",
    hail_size: float = 1.5,
    wind_speed: float = 0.0,
    county: str = "Thomasville, GA",
    zipcode: str = "31757",
    severity_score: float = 7.5,
    hours_ago: int = 12,
    distance: float = 20.0,
) -> str:
    """Insert a test storm event into the DB and return its id."""
    storm_id = str(uuid.uuid4())
    report_time = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO storm_events (
                id, zipcode, event_type, event_date, hail_size_inches,
                wind_speed_mph, source, county, report_time_utc,
                dedup_key, distance_miles_from_office, ingested_at,
                latitude, longitude, severity_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                storm_id, zipcode, event_type, report_time[:10],
                hail_size, wind_speed, "TEST",
                county, report_time,
                f"{event_type}|0.000|0.000|{report_time}|{storm_id[:8]}",
                distance, report_time,
                30.85, -84.00, severity_score,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return storm_id


@pytest.fixture(autouse=True)
def clean_storm_events():
    """Isolate storm_events rows for each test."""
    conn = get_connection()
    conn.execute("DELETE FROM storm_events")
    conn.commit()
    conn.close()
    yield
    conn = get_connection()
    conn.execute("DELETE FROM storm_events")
    conn.commit()
    conn.close()


class TestGetStormTargetSummaries:
    def test_empty_returns_empty_list(self):
        results = get_storm_target_summaries(window_hours=72)
        assert results == []

    def test_single_hail_event_returns_one_target(self):
        _insert_storm(event_type="HAIL", hail_size=1.5, severity_score=6.0, county="Alpha, GA", zipcode="31701")
        results = get_storm_target_summaries(window_hours=72)
        assert len(results) == 1
        target = results[0]
        assert target["location"] == "Alpha, GA"
        assert target["zipcode"] == "31701"
        assert target["event_count"] == 1
        assert target["max_hail_inches"] == 1.5
        assert target["has_tornado"] is False
        assert target["max_severity_score"] == 6.0

    def test_events_ranked_by_severity_desc(self):
        _insert_storm(county="Alpha, GA", zipcode="31701", severity_score=3.0, hail_size=0.75)
        _insert_storm(county="Beta, GA", zipcode="31792", severity_score=9.5, hail_size=2.0)
        _insert_storm(county="Gamma, GA", zipcode="31794", severity_score=6.0, hail_size=1.25)
        results = get_storm_target_summaries(window_hours=72)
        scores = [r["max_severity_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
        assert results[0]["location"] == "Beta, GA"

    def test_limit_parameter_respected(self):
        for i in range(5):
            _insert_storm(county=f"Area{i}, GA", zipcode=f"317{i:02d}", severity_score=float(i + 1))
        results = get_storm_target_summaries(window_hours=72, limit=3)
        assert len(results) <= 3

    def test_tornado_flag_set(self):
        _insert_storm(event_type="TORNADO", hail_size=0.0, wind_speed=0.0, severity_score=10.0, county="Delta, GA", zipcode="31788")
        results = get_storm_target_summaries(window_hours=72)
        assert any(r["has_tornado"] for r in results)

    def test_old_events_excluded_by_window(self):
        _insert_storm(hours_ago=200)  # Way outside 72-hour default window
        results = get_storm_target_summaries(window_hours=72)
        assert results == []

    def test_event_types_aggregated(self):
        _insert_storm(event_type="HAIL", county="Alpha, GA", zipcode="31701", severity_score=5.0)
        _insert_storm(event_type="WIND", wind_speed=65.0, hail_size=0.0, county="Alpha, GA", zipcode="31701", severity_score=4.0)
        results = get_storm_target_summaries(window_hours=72)
        assert len(results) == 1
        assert "HAIL" in results[0]["event_types"]
        assert results[0]["event_count"] == 2


class TestStormTargetsEndpoint:
    def test_requires_auth(self):
        resp = client.get("/api/office/storms/targets")
        assert resp.status_code == 401

    def test_field_rep_blocked(self):
        resp = client.get("/api/office/storms/targets", headers=FIELD_HEADERS)
        assert resp.status_code == 403

    def test_admin_can_access(self):
        resp = client.get("/api/office/storms/targets", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    def test_empty_targets_when_no_storms(self):
        resp = client.get("/api/office/storms/targets", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 0
        assert data["targets"] == []

    def test_targets_returned_with_storm_data(self):
        _insert_storm(hail_size=1.75, severity_score=8.0, county="Thomasville, GA", zipcode="31757")
        resp = client.get("/api/office/storms/targets?window_hours=72&limit=5", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        t = data["targets"][0]
        assert t["location"] == "Thomasville, GA"
        assert t["max_hail_inches"] == 1.75
        assert t["max_severity_score"] == 8.0

    def test_limit_query_param(self):
        for i in range(6):
            _insert_storm(county=f"Area{i}, GA", zipcode=f"317{i:02d}", severity_score=float(i + 1))
        resp = client.get("/api/office/storms/targets?limit=3", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] <= 3
