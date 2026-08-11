"""
Unit tests for the Office Control Center API routes.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
# Do not update headers directly, let the tests acquire cookies or we'll inject a cookie.
# For simplicity in TestClient without stateful cookies, we can just login.
response = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = response.cookies.get("auth_token")
client.cookies.set("auth_token", auth_cookie)

import pytest
@pytest.fixture(autouse=True)
def mock_pdf_detector():
    with patch("app.api.office_routes.detect_pdf_format", return_value="EAGLEVIEW"), \
         patch("app.core.pipeline.detect_pdf_format", return_value="EAGLEVIEW"):
        yield


class TestOfficeJobsRoute:
    """Tests for GET /api/office/jobs."""

    def test_office_routes_deny_field_token(self):
        """Should return 403 Forbidden if using a field token."""
        response = client.get("/api/office/jobs", headers={"x-internal-token": "1111"}) # This should fail or use cookie, wait, no. Let's just create a field token.
        from app.api.auth import create_access_token
        field_token = create_access_token("field")
        response = client.get("/api/office/jobs", cookies={"auth_token": field_token})
        assert response.status_code == 403

    @patch("app.api.office_routes.get_connection")
    def test_get_jobs_success(self, mock_get_connection):
        """Should return jobs properly parsed from SQLite."""
        
        # Mock the SQLite cursor and rows
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        
        # We simulate the sqlite3.Row behavior using a dict
        mock_cursor.fetchall.return_value = [
            {
                "id": "job-123",
                "homeowner_name": "John Doe",
                "address_line1": "123 Main St",
                "city": "Atlanta",
                "state": "GA",
                "postal_code": "30301",
                "phone": "555-0100",
                "email": "john@example.com",
                "claim_number": "CLM-999",
                "insurer_name": "State Farm",
                "status": "PHOTOS_UPLOADED",
                "status_history": '[{"status": "LEAD_CAPTURED", "timestamp": "2026-06-30T10:00:00Z"}]',
                "created_at": "2026-06-30 10:00:00"
            }
        ]
        
        mock_get_connection.return_value = mock_conn

        response = client.get("/api/office/jobs")
        
        assert response.status_code == 200
        data = response.json()
        
        assert len(data) == 1
        job = data[0]
        assert job["id"] == "job-123"
        assert job["homeowner_name"] == "John Doe"
        # Verify JSON decoding
        assert len(job["status_history"]) == 1
        assert job["status_history"][0]["status"] == "LEAD_CAPTURED"
        
        mock_conn.close.assert_called_once()

    @patch("app.api.office_routes.get_connection")
    def test_get_jobs_db_error(self, mock_get_connection):
        """Should return 500 if database query fails."""
        mock_conn = MagicMock()
        mock_conn.execute.side_effect = Exception("Database locked")
        mock_get_connection.return_value = mock_conn

        response = client.get("/api/office/jobs")
        
        assert response.status_code == 500
        assert "Failed to fetch jobs" in response.json()["detail"]
        
        mock_conn.close.assert_called_once()


class TestOfficeFinancialsRoute:
    @patch("app.api.office_routes.compute_job_profitability")
    @patch("app.api.office_routes.upsert_financials")
    @patch("app.api.office_routes.backup_database")
    def test_update_financials_background_backup(self, mock_backup, mock_upsert, mock_compute):
        """Verifies that backup_database is delegated to BackgroundTasks and executed."""
        mock_compute.return_value = {
            "gross_margin": 0.40, 
            "direct_costs_cents": 500000,
            "gross_profit_cents": 500000,
            "overhead_cost_cents": 250000,
            "net_profit_cents": 250000,
            "canvasser_commission_cents": 100000,
            "effective_commission_pct": 0.10
        }
        
        payload = {
            "revenue": 10000,
            "carrier_rcv": 10000,
            "materials": 3000,
            "labor": 2000,
            "overhead_pct": 0.25,
            "commission_pct": 0.10,
            "permits_fee": 0
        }
        
        response = client.post("/api/office/jobs/job-123/financials", json=payload)
        
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        
        # In FastAPI TestClient, background tasks are executed synchronously after response
        mock_backup.assert_called_once()

class TestUploadIdempotency:
    @patch("app.api.office_routes.stream_upload_safely")
    @patch("app.api.office_routes.get_job_document_by_hash")
    @patch("app.api.office_routes.run_full_office_pipeline")
    def test_upload_eagleview_idempotency(self, mock_run_pipeline, mock_get_doc, mock_stream):
        """Test that identical file uploads are short-circuited."""
        # 1. Simulate upload returning a specific hash
        mock_stream.return_value = "fake_sha256_hash"
        
        # 2. Simulate database already having this hash
        mock_get_doc.return_value = {"id": "doc123", "sha256_hash": "fake_sha256_hash"}
        
        # 3. Simulate file upload
        file_content = b"fake pdf content"
        response = client.post(
            "/api/office/jobs/99999999-9999-9999-9999-999999999903/eagleview",
            files={"file": ("eagleview.pdf", file_content, "application/pdf")}
        )
        
        # 4. Verify API response
        assert response.status_code == 200
        assert "Duplicate file detected" in response.json()["message"]
        
        # 5. Verify the pipeline was completely bypassed
        mock_run_pipeline.assert_not_called()


class TestSupplementUploadRoute:
    @patch("app.api.office_routes.insert_job_document")
    @patch("app.api.office_routes.stream_upload_safely")
    def test_supplement_upload_enqueues_full_generation(self, mock_stream, mock_insert):
        mock_stream.side_effect = ["ev_hash", "sol_hash"]
        mock_insert.side_effect = ["ev_doc_id", "sol_doc_id"]
        app.state.redis_pool = AsyncMock()

        response = client.post(
            "/api/office/jobs/99999999-9999-9999-9999-999999999904/supplement_docs",
            files={
                "ev_file": ("hover.pdf", b"%PDF-1.4 hover", "application/pdf"),
                "sol_file": ("sol.pdf", b"%PDF-1.4 sol", "application/pdf"),
            },
        )

        assert response.status_code == 200
        app.state.redis_pool.enqueue_job.assert_awaited_once()
        _, kwargs = app.state.redis_pool.enqueue_job.await_args
        assert kwargs["generate_pdf"] is True

    @patch("app.api.office_routes.run_supplement_pipeline", new_callable=AsyncMock)
    @patch("app.api.office_routes.insert_job_document")
    @patch("app.api.office_routes.stream_upload_safely")
    def test_supplement_upload_runs_inline_without_redis(self, mock_stream, mock_insert, mock_pipeline):
        mock_stream.side_effect = ["ev_hash", "sol_hash"]
        mock_insert.side_effect = ["ev_doc_id", "sol_doc_id"]
        if hasattr(app.state, "redis_pool"):
            app.state.redis_pool = None

        response = client.post(
            "/api/office/jobs/99999999-9999-9999-9999-999999999905/supplement_docs",
            files={
                "ev_file": ("eagleview.pdf", b"%PDF-1.4 ev", "application/pdf"),
                "sol_file": ("sol.pdf", b"%PDF-1.4 sol", "application/pdf"),
            },
        )

        assert response.status_code == 200
        mock_pipeline.assert_awaited_once()
        assert mock_pipeline.await_args.kwargs["generate_pdf"] is True


class TestEvidenceGridRoute:
    @patch("app.api.office_routes.PDFGenerator")
    @patch("app.api.office_routes.get_inspection_summary", new_callable=AsyncMock)
    def test_evidence_grid_regenerates_when_ai_analysis_exists(self, mock_summary, mock_pdf_generator, tmp_path):
        from types import SimpleNamespace
        import uuid

        from app.core.database import get_connection, insert_job_document

        job_id = str(uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, "Grid Test", "123 Grid St", "City", "GA", "31792", "555-5555"),
        )
        conn.commit()
        conn.close()

        old_grid = tmp_path / "old_evidence_grid.pdf"
        old_grid.write_bytes(b"%PDF-1.4 old")
        new_grid = tmp_path / "new_evidence_grid.pdf"
        new_grid.write_bytes(b"%PDF-1.4 new")

        insert_job_document(
            job_id,
            "evidence_grid.pdf",
            "application/pdf",
            str(old_grid),
            None,
            "field_safe",
            "EVIDENCE_GRID",
            True,
        )

        mock_summary.return_value = SimpleNamespace(
            job_id=job_id,
            analyses=[SimpleNamespace(filename="roof.jpg")],
            photos=[],
        )
        mock_pdf_generator.return_value.generate_evidence_grid = AsyncMock(return_value=str(new_grid))

        response = client.get(f"/api/office/jobs/{job_id}/evidence_grid")

        assert response.status_code == 200
        mock_pdf_generator.return_value.generate_evidence_grid.assert_awaited_once()

    def test_field_evidence_grid_namespace_exists(self, tmp_path):
        import uuid

        from app.core.database import get_connection, insert_job_document

        job_id = str(uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, "Field Grid", "124 Grid St", "City", "GA", "31792", "555-5555"),
        )
        conn.commit()
        conn.close()

        grid = tmp_path / "field_evidence_grid.pdf"
        grid.write_bytes(b"%PDF-1.4 field")
        insert_job_document(
            job_id,
            "evidence_grid.pdf",
            "application/pdf",
            str(grid),
            None,
            "field_safe",
            "EVIDENCE_GRID",
            True,
        )

        response = client.get(f"/api/field/jobs/{job_id}/evidence_grid")

        assert response.status_code == 200

class TestMaterialOrderIntegration:
    def test_material_order_route_integration(self):
        """Verify the material_order route successfully imports its dependencies and runs."""
        payload = {
            "supplier_name": "ABC Supply",
            "delivery_date": "2026-08-01"
        }
        
        # We expect it to fail gracefully with a ValueError about the missing PDF,
        # NOT crash with an ImportError (which returns 500).
        response = client.post("/api/office/jobs/job-123/material_order", json=payload)
        
        assert response.status_code == 400
        assert "EagleView PDF not found" in response.json()["detail"]

def test_mark_commission_paid(monkeypatch):
    from app.core.database import get_connection
    import uuid
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, commission_ready) VALUES (?, 'Test', '123 Test', 'TestCity', 'TS', '12345', '555', 'LEAD_CAPTURED', 1)",
        (job_id,)
    )
    conn.commit()
    conn.close()

    response = client.patch(f"/api/office/accounting/jobs/{job_id}/commission/paid")
    assert response.status_code == 200
    
    conn = get_connection()
    row = conn.execute("SELECT commission_ready FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["commission_ready"] == 0
    conn.close()
