"""
Tests for the neighbor letter PDF generator.
"""
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest


def _make_job(status: str = "INSTALL_COMPLETED") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "homeowner_name": "Jane Smith",
        "address_line1": "456 Oak Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31757",
        "phone": "5551234567",
        "status": status,
    }


SAMPLE_STORM_EVENTS = [
    {
        "event_type": "HAIL",
        "location": "Thomasville, GA",
        "county": "Thomasville, GA",
        "last_event_utc": "2026-08-10T14:30:00Z",
        "max_hail_inches": 1.75,
        "max_wind_mph": 45.0,
    }
]


@pytest.mark.asyncio
async def test_neighbor_letter_generates_pdf(tmp_path):
    """Neighbor letter generator produces a non-empty PDF file."""
    from app.services.pdf.neighbor_letter import NeighborLetterGenerator

    job = _make_job()
    job_id = job["id"]

    # Patch FIELD_DOCS_DIR to use tmp_path
    with patch("app.services.pdf.neighbor_letter.FIELD_DOCS_DIR", tmp_path):
        gen = NeighborLetterGenerator()
        pdf_path = await gen.generate(job, SAMPLE_STORM_EVENTS)

    assert Path(pdf_path).exists(), "PDF file should exist on disk"
    assert Path(pdf_path).stat().st_size > 1024, "PDF should be non-trivially small"
    assert pdf_path.endswith(".pdf")


@pytest.mark.asyncio
async def test_neighbor_letter_without_storm_events(tmp_path):
    """Generator works gracefully when no storm events are provided."""
    from app.services.pdf.neighbor_letter import NeighborLetterGenerator

    job = _make_job()

    with patch("app.services.pdf.neighbor_letter.FIELD_DOCS_DIR", tmp_path):
        gen = NeighborLetterGenerator()
        pdf_path = await gen.generate(job, storm_events=[])

    assert Path(pdf_path).exists()
    assert Path(pdf_path).stat().st_size > 512


@pytest.mark.asyncio
async def test_neighbor_letter_with_multiple_storm_events(tmp_path):
    """Generator caps storm event context at 3 events without error."""
    from app.services.pdf.neighbor_letter import NeighborLetterGenerator

    job = _make_job()
    events = [
        {"event_type": "HAIL", "county": f"County{i}, GA", "max_hail_inches": 1.0 + i * 0.25,
         "max_wind_mph": 0.0, "last_event_utc": "2026-08-10T14:30:00Z"}
        for i in range(5)
    ]

    with patch("app.services.pdf.neighbor_letter.FIELD_DOCS_DIR", tmp_path):
        gen = NeighborLetterGenerator()
        pdf_path = await gen.generate(job, storm_events=events)

    assert Path(pdf_path).exists()


@pytest.mark.asyncio
async def test_neighbor_letter_filename(tmp_path):
    """Generated file is named 'Neighbor_Letter.pdf'."""
    from app.services.pdf.neighbor_letter import NeighborLetterGenerator

    job = _make_job()
    with patch("app.services.pdf.neighbor_letter.FIELD_DOCS_DIR", tmp_path):
        gen = NeighborLetterGenerator()
        pdf_path = await gen.generate(job)

    assert Path(pdf_path).name == "Neighbor_Letter.pdf"


@pytest.fixture(autouse=True)
def clean_db():
    from app.core.database import get_connection
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM storm_events")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM storm_events")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()


def _insert_job(job_id: str, zipcode: str, status: str = "INSTALL_COMPLETED") -> dict:
    from app.core.database import get_connection
    conn = get_connection()
    try:
        conn.execute("INSERT OR REPLACE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (job_id, "Jane Smith", "456 Oak Lane", "Thomasville", "GA", zipcode, "5551234567", status))
        conn.commit()
    finally:
        conn.close()
    return {
        "id": job_id,
        "homeowner_name": "Jane Smith",
        "address_line1": "456 Oak Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": zipcode,
        "phone": "5551234567",
        "status": status,
    }


def _insert_test_storm(zipcode: str, event_type: str, severity_score: float, county: str) -> None:
    from app.core.database import get_connection
    import datetime
    from datetime import UTC
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO storm_events (
                id, zipcode, event_type, event_date, hail_size_inches,
                wind_speed_mph, source, county, report_time_utc,
                dedup_key, distance_miles_from_office, ingested_at,
                latitude, longitude, severity_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), zipcode, event_type, "2026-08-27",
            1.75 if event_type == "HAIL" else 0.0,
            60.0 if event_type == "WIND" else 0.0,
            "NWS", county, datetime.datetime.now(UTC).isoformat(),
            str(uuid.uuid4()), 10.0, datetime.datetime.now(UTC).isoformat(),
            30.85, -84.00, severity_score
        ))
        conn.commit()
    finally:
        conn.close()


class TestNeighborLetterEndpoint:
    @pytest.mark.asyncio
    async def test_neighbor_letter_endpoint_uses_job_local_storms(self, tmp_path, monkeypatch):
        # Setup tmp field_docs dir so we don't write to real data/
        monkeypatch.setattr("app.api.field_routes.FIELD_DOCS_DIR", tmp_path)
        monkeypatch.setattr("app.services.pdf.neighbor_letter.FIELD_DOCS_DIR", tmp_path)
        
        job_id = str(uuid.uuid4())
        # job is in 31757
        _insert_job(job_id, "31757")
        
        # Insert a storm in 31757 (should be matched)
        _insert_test_storm("31757", "HAIL", 8.0, "Thomas County")
        # Insert a storm in another ZIP 30301 (should NOT be matched)
        _insert_test_storm("30301", "WIND", 9.0, "Fulton County")
        
        # Directly query the storm events near this job
        from app.core.database import get_storm_events_near_job
        local_events = get_storm_events_near_job(job_id)
        assert len(local_events) == 1
        assert local_events[0]["zipcode"] == "31757"
        assert local_events[0]["county"] == "Thomas County"
        
        # Call the endpoint
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.auth import create_access_token
        
        client = TestClient(app)
        token = create_access_token("field")
        headers = {"x-internal-token": token}
        
        # We mock assert_field_rep_owns_job to bypass ownership check
        with patch("app.api.field_routes.assert_field_rep_owns_job"), \
             patch("app.api.field_routes.insert_job_document"): # mock vault insert to simplify
            resp = client.get(f"/api/field/jobs/{job_id}/docs/neighbor-letter", headers=headers)
            
        assert resp.status_code == 200

