"""
Tests for the Sales Pipeline Summary helper and endpoint.
"""
import json
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_connection, get_sales_pipeline_summary
from app.main import app

from app.api.auth import create_access_token

client = TestClient(app)

# Generate valid JWT tokens for tests
admin_token = create_access_token("admin")
ADMIN_HEADERS = {"x-internal-token": admin_token}


def _create_job(status: str = "LEAD_CAPTURED", canvasser: str = "") -> str:
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs
               (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "Test Owner", "123 Main St", "Thomasville", "GA", "31757", "5551234567", status, canvasser or None),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


@pytest.fixture(autouse=True)
def clean_jobs():
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM jobs")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    finally:
        conn.close()


class TestGetSalesPipelineSummary:
    def test_empty_db_returns_zeros(self):
        result = get_sales_pipeline_summary()
        assert isinstance(result["stage_counts"], dict)
        assert result["total_active"] == 0
        assert result["rep_metrics"] == []
        assert result["avg_speed_to_lead_hours"] is None

    def test_stage_counts_only_contains_valid_job_statuses(self):
        from app.core.database import JobStatus
        result = get_sales_pipeline_summary()
        stages = list(result["stage_counts"].keys())
        for stage in stages:
            assert stage in JobStatus.__members__


    def test_counts_jobs_by_stage(self):
        _create_job("LEAD_CAPTURED")
        _create_job("LEAD_CAPTURED")
        _create_job("CONTINGENCY_SIGNED")
        result = get_sales_pipeline_summary()
        assert result["stage_counts"]["LEAD_CAPTURED"] == 2
        assert result["stage_counts"]["CONTINGENCY_SIGNED"] == 1

    def test_total_active_excludes_closed(self):
        _create_job("LEAD_CAPTURED")
        _create_job("CLOSED")
        result = get_sales_pipeline_summary()
        # LEAD_CAPTURED counts as active; CLOSED does not
        assert result["total_active"] == 1

    def test_rep_metrics_aggregated(self):
        _create_job("LEAD_CAPTURED", canvasser="Alice")
        _create_job("CONTINGENCY_SIGNED", canvasser="Alice")
        _create_job("LEAD_CAPTURED", canvasser="Bob")
        result = get_sales_pipeline_summary()
        reps = {r["rep_name"]: r for r in result["rep_metrics"]}
        assert "Alice" in reps
        assert reps["Alice"]["leads"] == 2
        assert reps["Alice"]["contingencies"] == 1
        assert "Bob" in reps
        assert reps["Bob"]["leads"] == 1

    def test_speed_to_lead_computed_from_status_history(self):
        # Create a job with 2-hour gap between statuses
        job_id = str(uuid.uuid4())
        t0 = datetime(2026, 1, 1, 10, 0, 0)
        t1 = datetime(2026, 1, 1, 12, 0, 0)
        history = json.dumps([
            {"status": "LEAD_CAPTURED", "timestamp": t0.isoformat(), "note": ""},
            {"status": "CONTINGENCY_SIGNED", "timestamp": t1.isoformat(), "note": ""},
        ])
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO jobs
                   (id, homeowner_name, address_line1, city, state, postal_code, phone, status, status_history)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "Test", "123 A", "City", "GA", "31757", "5551234567", "CONTINGENCY_SIGNED", history),
            )
            conn.commit()
        finally:
            conn.close()

        result = get_sales_pipeline_summary()
        assert result["avg_speed_to_lead_hours"] == pytest.approx(2.0, abs=0.01)

    def test_malformed_history_skipped_gracefully(self):
        job_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO jobs
                   (id, homeowner_name, address_line1, city, state, postal_code, phone, status, status_history)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "Test", "123 A", "City", "GA", "31757", "5551234567", "CONTINGENCY_SIGNED", "INVALID_JSON"),
            )
            conn.commit()
        finally:
            conn.close()
        # Should not raise; avg_speed_to_lead_hours returns None when no valid samples
        result = get_sales_pipeline_summary()
        assert result["avg_speed_to_lead_hours"] is None


class TestPipelineSummaryEndpoint:
    def test_requires_admin(self):
        resp = client.get("/api/office/pipeline/summary")
        assert resp.status_code == 401

    def test_returns_pipeline_structure(self):
        resp = client.get("/api/office/pipeline/summary", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "stage_counts" in data
        assert "rep_metrics" in data
        assert "total_active" in data
        assert "avg_speed_to_lead_hours" in data

    def test_reflects_created_jobs(self):
        _create_job("LEAD_CAPTURED")
        _create_job("CLOSED")
        resp = client.get("/api/office/pipeline/summary", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["stage_counts"]["LEAD_CAPTURED"] >= 1
        assert data["stage_counts"]["CLOSED"] >= 1


class TestFieldPipelineSummaryEndpoint:
    def test_requires_auth(self):
        resp = client.get("/api/field/pipeline/summary")
        assert resp.status_code == 401

    def test_returns_pipeline_for_logged_in_rep(self):
        _create_job("LEAD_CAPTURED", canvasser="Alice")
        _create_job("CONTINGENCY_SIGNED", canvasser="Alice")
        _create_job("LEAD_CAPTURED", canvasser="Bob")

        alice_token = create_access_token("field", rep_name="Alice")
        headers = {"x-internal-token": alice_token}
        resp = client.get("/api/field/pipeline/summary", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        # Only Alice's jobs should be summarized
        assert data["stage_counts"]["LEAD_CAPTURED"] == 1
        assert data["stage_counts"]["CONTINGENCY_SIGNED"] == 1
        assert data["total_active"] == 2

    def test_speed_to_lead_for_rep(self):
        # Create a job for Alice with history
        job_id = str(uuid.uuid4())
        t0 = datetime(2026, 1, 1, 10, 0, 0)
        t1 = datetime(2026, 1, 1, 13, 0, 0)
        history = json.dumps([
            {"status": "LEAD_CAPTURED", "timestamp": t0.isoformat(), "note": ""},
            {"status": "CONTINGENCY_SIGNED", "timestamp": t1.isoformat(), "note": ""},
        ])
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO jobs
                   (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name, status_history)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, "Test Rep Job", "123 B St", "City", "GA", "31757", "5551234567", "CONTINGENCY_SIGNED", "Alice", history),
            )
            conn.commit()
        finally:
            conn.close()

        alice_token = create_access_token("field", rep_name="Alice")
        headers = {"x-internal-token": alice_token}
        resp = client.get("/api/field/pipeline/summary", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["avg_speed_to_lead_hours"] == pytest.approx(3.0, abs=0.01)
