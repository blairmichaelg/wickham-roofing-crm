"""
Unit tests for the generic event trigger endpoint.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------
VALID_API_KEY = "test_webhook_secret_12345"


def _make_test_app():
    """
    Create a fresh FastAPI app with mocked external dependencies.
    """
    mock_settings = MagicMock()
    mock_settings.webhook_secret = VALID_API_KEY
    mock_settings.redis_url = "redis://localhost:6379"
    mock_settings.gemini_api_key = "test_gemini_key"
    mock_settings.app_env = "development"
    mock_settings.log_level = "DEBUG"


    from fastapi import FastAPI

    from app.api.webhooks import router
    from app.config import get_settings

    test_app = FastAPI()
    test_app.include_router(router)

    test_app.dependency_overrides[get_settings] = lambda: mock_settings

    mock_pool = AsyncMock()
    mock_pool.enqueue_job = AsyncMock(return_value=MagicMock(job_id="test_job_123"))
    test_app.state.redis_pool = mock_pool

    return test_app, mock_pool


# ---------------------------------------------------------------------------
# Test: Security
# ---------------------------------------------------------------------------
class TestEventSecurity:
    def test_missing_api_key_returns_401(self):
        app, _ = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={"job_id": "abc123", "event_type": "supplement", "ev_pdf_path": "path", "sol_pdf_path": "path"},
        )

        assert response.status_code == 401
        assert "Missing" in response.json()["detail"] or "x-api-key" in response.json()["detail"]

    def test_wrong_api_key_returns_401(self):
        app, _ = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={"job_id": "abc123", "event_type": "supplement", "ev_pdf_path": "path", "sol_pdf_path": "path"},
            headers={"x-api-key": "wrong_key_entirely"},
        )

        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

    def test_valid_api_key_passes(self):
        app, _ = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={"job_id": "abc123", "event_type": "supplement", "ev_pdf_path": "path", "sol_pdf_path": "path"},
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test: Routing and Validation
# ---------------------------------------------------------------------------
class TestEventRouting:
    def test_supplement_event_enqueued(self):
        app, mock_pool = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={
                "job_id": "job_xyz",
                "event_type": "supplement",
                "ev_pdf_path": "/tmp/ev.pdf",
                "sol_pdf_path": "/tmp/sol.pdf"
            },
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["job_id"] == "job_xyz"

        mock_pool.enqueue_job.assert_called_once_with(
            "process_supplement_event",
            job_id="job_xyz",
            ev_pdf_path="/tmp/ev.pdf",
            sol_pdf_path="/tmp/sol.pdf",
            role="admin"
        )

    def test_supplement_missing_pdfs_ignored(self):
        app, mock_pool = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={
                "job_id": "job_xyz",
                "event_type": "supplement",
                # missing paths
            },
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "missing_pdf_paths"

        mock_pool.enqueue_job.assert_not_called()

    def test_inspection_event_ignored_for_now(self):
        app, mock_pool = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={
                "job_id": "job_xyz",
                "event_type": "inspection",
            },
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "inspection_event_not_implemented"

        mock_pool.enqueue_job.assert_not_called()

    def test_unknown_event_type_ignored(self):
        app, mock_pool = _make_test_app()
        client = TestClient(app)

        response = client.post(
            "/events/trigger",
            json={
                "job_id": "job_xyz",
                "event_type": "something_else",
            },
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"
        assert data["reason"] == "unknown_event_type"

        mock_pool.enqueue_job.assert_not_called()

    def test_enqueue_failure_returns_503(self):
        app, mock_pool = _make_test_app()
        client = TestClient(app)

        mock_pool.enqueue_job.side_effect = Exception("Redis connection failed")

        response = client.post(
            "/events/trigger",
            json={
                "job_id": "job_503",
                "event_type": "supplement",
                "ev_pdf_path": "/tmp/ev.pdf",
                "sol_pdf_path": "/tmp/sol.pdf"
            },
            headers={"x-api-key": VALID_API_KEY},
        )

        assert response.status_code == 503
        assert "Service Unavailable" in response.json()["detail"]

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
