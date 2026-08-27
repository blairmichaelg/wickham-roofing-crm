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
