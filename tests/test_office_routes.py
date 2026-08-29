"""
Unit tests for the Office Control Center API routes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_connection
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
    def test_supplement_upload_enqueues_full_generation(self):
        import uuid
        job_id = str(uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Hover Homeowner', '101 Hover St', 'Valdosta', 'GA', '31601', '555-0101', 'LEAD_CAPTURED', 'INSURANCE')",
            (job_id,)
        )
        conn.commit()
        conn.close()

        app.state.redis_pool = AsyncMock()

        try:
            response = client.post(
                f"/api/office/jobs/{job_id}/supplement_docs",
                files={
                    "ev_file": ("hover.pdf", b"%PDF-1.4 hover mock content", "application/pdf"),
                    "sol_file": ("sol.pdf", b"%PDF-1.4 sol mock content", "application/pdf"),
                },
            )

            assert response.status_code == 200
            app.state.redis_pool.enqueue_job.assert_awaited_once()
            _, kwargs = app.state.redis_pool.enqueue_job.await_args
            assert kwargs["generate_pdf"] is True
            assert uuid.UUID(kwargs["ev_doc_id"])
            assert uuid.UUID(kwargs["sol_doc_id"])
            assert len(kwargs["ev_sha256"]) == 64
            assert len(kwargs["sol_sha256"]) == 64
        finally:
            conn = get_connection()
            conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            conn.close()

    @patch("app.api.office_routes.run_supplement_pipeline", new_callable=AsyncMock)
    def test_supplement_upload_runs_inline_without_redis(self, mock_pipeline):
        import uuid
        job_id = str(uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'EV Homeowner', '102 EV St', 'Valdosta', 'GA', '31601', '555-0102', 'LEAD_CAPTURED', 'INSURANCE')",
            (job_id,)
        )
        conn.commit()
        conn.close()

        if hasattr(app.state, "redis_pool"):
            app.state.redis_pool = None

        try:
            response = client.post(
                f"/api/office/jobs/{job_id}/supplement_docs",
                files={
                    "ev_file": ("eagleview.pdf", b"%PDF-1.4 ev mock content", "application/pdf"),
                    "sol_file": ("sol.pdf", b"%PDF-1.4 sol mock content", "application/pdf"),
                },
            )

            assert response.status_code == 200
            mock_pipeline.assert_awaited_once()
            kwargs = mock_pipeline.await_args.kwargs
            assert kwargs["generate_pdf"] is True
            assert uuid.UUID(kwargs["ev_doc_id"])
            assert uuid.UUID(kwargs["sol_doc_id"])
            assert len(kwargs["ev_sha256"]) == 64
            assert len(kwargs["sol_sha256"]) == 64
        finally:
            conn = get_connection()
            conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            conn.commit()
            conn.close()


class TestEvidenceGridRoute:
    @patch("app.api.office_routes.PDFGenerator")
    @patch("app.api.office_routes.get_inspection_summary", new_callable=AsyncMock)
    def test_evidence_grid_regenerates_when_ai_analysis_exists(self, mock_summary, mock_pdf_generator, tmp_path):
        import uuid
        from types import SimpleNamespace

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
    import uuid

    from app.core.database import get_connection
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


def test_admin_triage_view_surfaces_review_and_failed_jobs():
    """Verify that /api/office/admin/triage surfaces PENDING_OPERATOR_REVIEW, PIPELINE_FAILED, and INSPECTION_FAILED jobs, and excludes normal jobs."""
    from app.api.auth import create_access_token
    from app.core.database import get_connection
    import uuid

    admin_token = create_access_token("admin")
    job_review = f"job-rev-{uuid.uuid4().hex[:8]}"
    job_failed = f"job-fail-{uuid.uuid4().hex[:8]}"
    job_insp_failed = f"job-insp-{uuid.uuid4().hex[:8]}"
    job_normal = f"job-norm-{uuid.uuid4().hex[:8]}"

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Review Homeowner', '123 Review Rd', 'Valdosta', 'GA', '31601', '555-1111', 'PENDING_OPERATOR_REVIEW')",
        (job_review,)
    )
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Failed Homeowner', '456 Failed Rd', 'Valdosta', 'GA', '31601', '555-2222', 'PIPELINE_FAILED')",
        (job_failed,)
    )
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Inspection Failed Homeowner', '789 Inspection Rd', 'Valdosta', 'GA', '31601', '555-3333', 'INSPECTION_FAILED')",
        (job_insp_failed,)
    )
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Normal Lead Homeowner', '999 Normal Rd', 'Valdosta', 'GA', '31601', '555-4444', 'LEAD_CAPTURED')",
        (job_normal,)
    )
    conn.commit()
    conn.close()

    try:
        response = client.get("/api/office/admin/triage", cookies={"auth_token": admin_token})
        assert response.status_code == 200
        # (a) Pre-existing PENDING_OPERATOR_REVIEW status surfaces
        assert "Review Homeowner" in response.text
        # (b) PIPELINE_FAILED status surfaces with distinct badge
        assert "Failed Homeowner" in response.text
        assert "PIPELINE FAILED" in response.text
        # (c) INSPECTION_FAILED status surfaces with distinct badge
        assert "Inspection Failed Homeowner" in response.text
        assert "INSPECTION FAILED" in response.text
        # (d) Normal/unrelated status does NOT surface in triage
        assert "Normal Lead Homeowner" not in response.text
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM jobs WHERE id IN (?, ?, ?, ?)", (job_review, job_failed, job_insp_failed, job_normal))
        conn.commit()
        conn.close()


def test_manual_measurement_entry_success():
    from app.api.auth import create_access_token
    import uuid
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Manual Geometry Homeowner', '100 Geometry Way', 'Valdosta', 'GA', '31601', '555-1000', 'LEAD_CAPTURED')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    try:
        payload = {
            "total_area_sf": 2800.0,
            "predominant_pitch": "6/12",
            "ridge_lf": 45.0,
            "hip_lf": 60.0,
            "valley_lf": 30.0,
            "eaves_lf": 150.0,
            "rake_lf": 80.0,
            "drip_edge_lf": 230.0,
            "flashing_lf": 25.0,
            "step_flashing_lf": 35.0,
            "flashing_wall_lf": 15.0,
            "total_facets": 8,
            "pipe_boot_count": 3,
            "vent_count": 4,
            "starter_strip_lf": 230.0
        }
        res = client.post(
            f"/api/office/jobs/{job_id}/measurements/manual",
            json=payload,
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"

        # Verify DB state
        conn = get_connection()
        row = conn.execute(
            "SELECT status, ev_total_area_sf, ev_predominant_pitch, ev_ridge_lf, ev_drip_edge_lf, ev_total_facets, ev_pipe_boot_count, ev_vent_count, ev_starter_strip_lf, ev_flashing_wall_lf FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        conn.close()

        assert row["status"] == "EV_PARSED"
        assert row["ev_total_area_sf"] == 2800.0
        assert row["ev_predominant_pitch"] == "6/12"
        assert row["ev_ridge_lf"] == 45.0
        assert row["ev_drip_edge_lf"] == 230.0
        assert row["ev_total_facets"] == 8
        assert row["ev_pipe_boot_count"] == 3
        assert row["ev_vent_count"] == 4
        assert row["ev_starter_strip_lf"] == 230.0
        assert row["ev_flashing_wall_lf"] == 15.0
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_manual_measurement_entry_invalid_geometry_rejected():
    from app.api.auth import create_access_token
    import uuid
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Bad Geometry Homeowner', '101 Error Way', 'Valdosta', 'GA', '31601', '555-1001', 'LEAD_CAPTURED')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    try:
        # Impossible footprint vs perimeter: 10,000 SF on 20 LF perimeter
        payload = {
            "total_area_sf": 10000.0,
            "predominant_pitch": "6/12",
            "ridge_lf": 0.0,
            "hip_lf": 0.0,
            "valley_lf": 0.0,
            "eaves_lf": 10.0,
            "rake_lf": 10.0
        }
        res = client.post(
            f"/api/office/jobs/{job_id}/measurements/manual",
            json=payload,
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 422
        assert "Impossible geometry" in res.json()["detail"]
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_admin_triage_resolve_with_geometry_validation():
    from app.api.auth import create_access_token
    import uuid
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) VALUES (?, 'Triage Resolve Homeowner', '102 Triage Way', 'Valdosta', 'GA', '31601', '555-1002', 'PIPELINE_FAILED')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    class MockRedis:
        async def enqueue_job(self, *args, **kwargs):
            return "job_queued"

    from app.main import app
    app.state.redis_pool = MockRedis()

    try:
        # 1. Invalid geometry rejected with 422
        bad_payload = {
            "ev_total_area_sf": 5000.0,
            "ev_predominant_pitch": "30/12",  # invalid pitch
            "ev_eaves_lf": 100.0,
            "ev_rakes_lf": 50.0
        }
        res = client.post(
            f"/api/office/admin/triage/{job_id}/resolve",
            json=bad_payload,
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 422

        # 2. Valid geometry succeeds and queues ARQ retry
        good_payload = {
            "ev_total_area_sf": 2500.0,
            "ev_predominant_pitch": "6/12",
            "ev_ridge_lf": 40.0,
            "ev_eaves_lf": 150.0,
            "ev_rakes_lf": 80.0,
            "ev_drip_edge_lf": 230.0,
            "ev_total_facets": 6
        }
        res_ok = client.post(
            f"/api/office/admin/triage/{job_id}/resolve",
            json=good_payload,
            cookies={"auth_token": admin_token}
        )
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "queued"
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()




