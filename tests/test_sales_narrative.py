"""
Tests for the AI Sales Narrative service.

All tests mock the GeminiClient to avoid actual API calls and validate:
- Prompt grounding (real data is passed, no invented details)
- Graceful fallback when GeminiClient fails
- Output type and non-emptiness
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.sales_narrative import (
    _build_context_block,
    generate_door_script,
    generate_sales_summary,
)


def _make_job(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "homeowner_name": "Jane Smith",
        "address_line1": "456 Oak Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31757",
        "status": "LEAD_CAPTURED",
        "loss_date": "2026-08-10",
        "insurer_name": "State Farm",
    }
    base.update(overrides)
    return base


SAMPLE_EVENTS = [
    {
        "event_type": "HAIL",
        "county": "Thomasville, GA",
        "max_hail_inches": 1.75,
        "max_wind_mph": 45.0,
        "last_event_utc": "2026-08-10T14:30:00Z",
    }
]


class TestBuildContextBlock:
    def test_includes_address(self):
        job = _make_job()
        ctx = _build_context_block(job, [])
        assert "456 Oak Lane" in ctx
        assert "Thomasville" in ctx
        assert "GA" in ctx

    def test_includes_loss_date(self):
        job = _make_job()
        ctx = _build_context_block(job, [])
        assert "2026-08-10" in ctx

    def test_includes_storm_details(self):
        job = _make_job()
        ctx = _build_context_block(job, SAMPLE_EVENTS)
        assert "HAIL" in ctx
        assert "1.75" in ctx
        assert "Thomasville" in ctx

    def test_no_storm_events_message(self):
        job = _make_job()
        ctx = _build_context_block(job, [])
        assert "No specific storm events" in ctx

    def test_caps_storm_events_at_three(self):
        job = _make_job()
        events = [
            {"event_type": "HAIL", "county": f"Area{i}", "max_hail_inches": 1.0, "max_wind_mph": 0.0, "last_event_utc": ""}
            for i in range(6)
        ]
        ctx = _build_context_block(job, events)
        # Only 3 events should be referenced (check count of "HAIL" lines)
        hail_lines = [line for line in ctx.split("\n") if "HAIL event" in line]
        assert len(hail_lines) <= 3


@pytest.mark.asyncio
async def test_generate_sales_summary_returns_string():
    job = _make_job()
    with patch("app.services.ai_service.GeminiClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.generate_text = AsyncMock(return_value="Great sales summary here.")
        result = await generate_sales_summary(job, SAMPLE_EVENTS)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_generate_door_script_returns_string():
    job = _make_job()
    with patch("app.services.ai_service.GeminiClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.generate_text = AsyncMock(return_value="Hello, I'm from Wickham Roofing...")
        result = await generate_door_script(job, SAMPLE_EVENTS)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_generate_sales_summary_fallback_on_error():
    """When GeminiClient raises, a graceful fallback string is returned (no exception)."""
    job = _make_job()
    with patch("app.services.ai_service.GeminiClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.generate_text = AsyncMock(side_effect=Exception("API failure"))
        result = await generate_sales_summary(job, SAMPLE_EVENTS)
    # Should return a non-empty fallback string, not raise
    assert isinstance(result, str)
    assert len(result) > 0
    assert "Wickham Roofing" in result


@pytest.mark.asyncio
async def test_generate_door_script_fallback_on_error():
    """When GeminiClient raises, a graceful fallback door script is returned."""
    job = _make_job()
    with patch("app.services.ai_service.GeminiClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.generate_text = AsyncMock(side_effect=RuntimeError("timeout"))
        result = await generate_door_script(job, SAMPLE_EVENTS)
    assert isinstance(result, str)
    assert "Wickham Roofing" in result


@pytest.mark.asyncio
async def test_prompt_is_grounded_with_real_data():
    """Verify the prompt sent to GeminiClient contains the actual job address."""
    job = _make_job()
    captured_prompts: list[str] = []

    async def mock_generate(system_prompt: str, user_prompt: str) -> str:
        captured_prompts.append(user_prompt)
        return "mocked response"

    with patch("app.services.ai_service.GeminiClient") as MockClient:
        mock_instance = MockClient.return_value
        mock_instance.generate_text = mock_generate
        await generate_sales_summary(job, SAMPLE_EVENTS)

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    # The prompt must contain the actual address — no invented data
    assert "456 Oak Lane" in prompt
    assert "Thomasville" in prompt


@pytest.fixture(autouse=True)
def clean_db():
    from app.core.database import get_connection
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM storm_events")
        conn.execute("DELETE FROM job_documents")
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
        conn.execute("DELETE FROM job_documents")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()


def _insert_job(job_id: str, zipcode: str, status: str = "LEAD_CAPTURED") -> dict:
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


class TestSalesNarrativeEndpoint:
    @pytest.mark.asyncio
    async def test_sales_tools_endpoint_uses_job_local_storms(self):
        job_id = str(uuid.uuid4())
        # job in 31757
        _insert_job(job_id, "31757")
        
        # Local storm (in 31757)
        _insert_test_storm("31757", "HAIL", 8.0, "Thomas County")
        # Unrelated storm (in 30301)
        _insert_test_storm("30301", "WIND", 9.0, "Fulton County")
        
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.auth import create_access_token
        
        client = TestClient(app)
        token = create_access_token("field")
        headers = {"x-internal-token": token}
        
        captured_prompts = []
        
        async def mock_generate(system_prompt: str, user_prompt: str) -> str:
            captured_prompts.append(user_prompt)
            return "mocked response"
            
        with patch("app.services.ai_service.GeminiClient") as MockClient, \
             patch("app.api.field_routes.assert_field_rep_owns_job"), \
             patch("app.api.field_routes.insert_job_document"):
            mock_instance = MockClient.return_value
            mock_instance.generate_text = mock_generate
            
            resp = client.get(f"/api/field/jobs/{job_id}/sales-tools", headers=headers)
            
        assert resp.status_code == 200
        # Check that we captured the prompt generated for generate_sales_summary and/or generate_door_script
        assert len(captured_prompts) > 0
        for prompt in captured_prompts:
            # Must reference the local county and storm details
            assert "Thomas County" in prompt
            assert "HAIL" in prompt or "1.75" in prompt
            
            # Must NOT reference fulton county or wind
            assert "Fulton County" not in prompt

