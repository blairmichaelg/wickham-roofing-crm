import io
import uuid
import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import get_connection
from app.core.utils import is_retail_job
from app.main import app


def test_is_retail_job_normalization():
    assert is_retail_job("RETAIL") is True
    assert is_retail_job("retail") is True
    assert is_retail_job("Retail") is True
    assert is_retail_job(" retail ") is True
    assert is_retail_job("ReTaIl") is True
    assert is_retail_job(None) is False
    assert is_retail_job("") is False
    assert is_retail_job("INSURANCE") is False
    assert is_retail_job("insurance") is False


def test_upload_measurement_report_retail_job_triggers_quote_pipeline(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type, ev_total_area_sf) VALUES (?, 'Retail Homeowner', '100 Retail Way', 'Valdosta', 'GA', '31601', '555-0101', 'LEAD_CAPTURED', 'RETAIL', 2500.0)",
        (job_id,)
    )
    conn.commit()
    conn.close()

    quote_pipeline_called = False

    async def mock_retail_quote(job_id=None, *args, **kwargs):
        nonlocal quote_pipeline_called
        quote_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.core.pipeline.run_retail_quote_pipeline", mock_retail_quote)
    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy eagleview content for test")
        res = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_pdf, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert "retail quote generation enqueued" in data["message"]
        assert quote_pipeline_called is True
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_upload_measurement_report_insurance_job_waits_for_sol(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Insurance Homeowner', '101 Ins Way', 'Valdosta', 'GA', '31601', '555-0102', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    supplement_pipeline_called = False

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal supplement_pipeline_called
        supplement_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy eagleview content for test 2")
        res = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_pdf, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert "Waiting for Statement of Loss" in data["message"]
        assert supplement_pipeline_called is False
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_upload_both_documents_insurance_job_triggers_supplement_pipeline(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Dual Doc Homeowner', '102 Dual Way', 'Valdosta', 'GA', '31601', '555-0103', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    supplement_pipeline_called = False

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal supplement_pipeline_called
        supplement_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        # 1. Upload SoL first
        dummy_sol = io.BytesIO(b"%PDF-1.4 dummy statement of loss content 1")
        res1 = client.post(
            f"/api/office/jobs/{job_id}/statement-of-loss",
            files={"file": ("statement_of_loss.pdf", dummy_sol, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res1.status_code == 200
        assert "Waiting for measurement report" in res1.json()["message"]
        assert supplement_pipeline_called is False

        # 2. Upload Measurement Report second -> triggers supplement pipeline
        dummy_ev = io.BytesIO(b"%PDF-1.4 dummy eagleview content 1")
        res2 = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_ev, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res2.status_code == 200
        assert "supplement generation enqueued" in res2.json()["message"]
        assert supplement_pipeline_called is True
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_idempotent_duplicate_upload(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Idempotent Homeowner', '103 Idem Way', 'Valdosta', 'GA', '31601', '555-0104', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        content = b"%PDF-1.4 exactly same identical file bytes for idempotency test"
        dummy1 = io.BytesIO(content)
        res1 = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("report.pdf", dummy1, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res1.status_code == 200

        dummy2 = io.BytesIO(content)
        res2 = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("report.pdf", dummy2, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res2.status_code == 200
        assert "Duplicate file detected" in res2.json()["message"]
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_trigger_supplement_route_routes_retail_job(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type, ev_total_area_sf) VALUES (?, 'Trigger Retail Homeowner', '104 Retail Way', 'Valdosta', 'GA', '31601', '555-0105', 'LEAD_CAPTURED', 'retail', 3000.0)",
        (job_id,)
    )
    conn.commit()
    conn.close()

    quote_pipeline_called = False

    async def mock_retail_quote(job_id=None, *args, **kwargs):
        nonlocal quote_pipeline_called
        quote_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.core.pipeline.run_retail_quote_pipeline", mock_retail_quote)
    app.state.redis_pool = None

    try:
        res = client.post(
            f"/api/office/jobs/{job_id}/trigger-supplement",
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "accepted"
        assert "Retail quote generation triggered" in data["message"]
        assert quote_pipeline_called is True
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_upload_supplement_docs_delegates_to_split_endpoints(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Wrapper Homeowner', '105 Wrapper Way', 'Valdosta', 'GA', '31601', '555-0106', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    supplement_pipeline_called = False

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal supplement_pipeline_called
        supplement_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    app.state.redis_pool = None

    try:
        dummy_ev = io.BytesIO(b"%PDF-1.4 dummy eagleview content")
        dummy_sol = io.BytesIO(b"%PDF-1.4 dummy statement of loss content")

        res = client.post(
            f"/api/office/jobs/{job_id}/supplement_docs",
            files={
                "ev_file": ("eagleview.pdf", dummy_ev, "application/pdf"),
                "sol_file": ("statement_of_loss.pdf", dummy_sol, "application/pdf")
            },
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert "via legacy wrapper" in data["message"]
        assert "measurement" in data
        assert "sol" in data
        assert supplement_pipeline_called is True
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_sequential_upload_passes_real_uuids_and_hashes_to_pipeline(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Real UUID Homeowner', '106 Real St', 'Valdosta', 'GA', '31601', '555-0107', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    pipeline_kwargs = {}

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal pipeline_kwargs
        pipeline_kwargs = kwargs
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    app.state.redis_pool = None

    try:
        dummy_ev = io.BytesIO(b"%PDF-1.4 real uuid eagleview content")
        dummy_sol = io.BytesIO(b"%PDF-1.4 real uuid statement of loss content")

        res1 = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_ev, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res1.status_code == 200

        res2 = client.post(
            f"/api/office/jobs/{job_id}/statement-of-loss",
            files={"file": ("statement_of_loss.pdf", dummy_sol, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res2.status_code == 200

        # Assert on the actual arguments passed to run_supplement_pipeline
        assert "ev_doc_id" in pipeline_kwargs
        assert "sol_doc_id" in pipeline_kwargs
        assert uuid.UUID(pipeline_kwargs["ev_doc_id"])
        assert uuid.UUID(pipeline_kwargs["sol_doc_id"])
        assert pipeline_kwargs["ev_doc_id"] != "ev_doc_id"
        assert pipeline_kwargs["sol_doc_id"] != "sol_doc_id"
        assert len(pipeline_kwargs["ev_sha256"]) == 64
        assert len(pipeline_kwargs["sol_sha256"]) == 64
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_orphaned_file_on_disk_without_db_row_does_not_trigger_pipeline(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Orphan Test Homeowner', '107 Orphan Way', 'Valdosta', 'GA', '31601', '555-0108', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    # Create an orphaned file on disk without registering it in job_documents
    from pathlib import Path
    orphan_dir = Path("data/field_docs") / job_id
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan_sol = orphan_dir / "statement_of_loss.pdf"
    orphan_sol.write_bytes(b"%PDF-1.4 orphaned sol content without db entry")

    pipeline_called = False

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        dummy_ev = io.BytesIO(b"%PDF-1.4 new eagleview content")
        res = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_ev, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200
        data = res.json()
        # Must wait for Statement of Loss and NOT trigger pipeline via orphaned file
        assert "Waiting for Statement of Loss" in data["message"]
        assert pipeline_called is False
    finally:
        if orphan_sol.exists():
            orphan_sol.unlink(missing_ok=True)
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_soft_deleted_document_excluded_from_readiness_check(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Soft Delete Homeowner', '108 Soft St', 'Valdosta', 'GA', '31601', '555-0109', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    # Insert a soft-deleted Statement of Loss
    conn.execute(
        """INSERT INTO job_documents (id, job_id, filename, file_type, storage_path, sha256_hash, visibility, category, deleted_at)
           VALUES (?, ?, 'old_sol.pdf', 'SOL_PDF', 'data/field_docs/dummy.pdf', 'dummy_hash', 'office_only', 'STATEMENT_OF_LOSS', CURRENT_TIMESTAMP)""",
        (str(uuid.uuid4()), job_id)
    )
    conn.commit()
    conn.close()

    pipeline_called = False

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal pipeline_called
        pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        dummy_ev = io.BytesIO(b"%PDF-1.4 new eagleview content")
        res = client.post(
            f"/api/office/jobs/{job_id}/measurement-report",
            files={"file": ("eagleview.pdf", dummy_ev, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200
        data = res.json()
        # Soft-deleted document must be excluded, so it waits for active Statement of Loss
        assert "Waiting for Statement of Loss" in data["message"]
        assert pipeline_called is False
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_upload_supplement_docs_retail_job_message_accuracy(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Retail Wrapper Homeowner', '109 Retail Way', 'Valdosta', 'GA', '31601', '555-0110', 'LEAD_CAPTURED', 'retail')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    retail_quote_called = False
    supplement_pipeline_called = False

    async def mock_retail_quote(job_id=None, *args, **kwargs):
        nonlocal retail_quote_called
        retail_quote_called = True
        return {"status": "success"}

    async def mock_supplement_pipeline(*args, **kwargs):
        nonlocal supplement_pipeline_called
        supplement_pipeline_called = True
        return {"status": "success"}

    monkeypatch.setattr("app.core.pipeline.run_retail_quote_pipeline", mock_retail_quote)
    monkeypatch.setattr("app.api.office_routes.run_supplement_pipeline", mock_supplement_pipeline)
    app.state.redis_pool = None

    try:
        dummy_ev = io.BytesIO(b"%PDF-1.4 dummy eagleview content")
        dummy_sol = io.BytesIO(b"%PDF-1.4 dummy statement of loss content")

        res = client.post(
            f"/api/office/jobs/{job_id}/supplement_docs",
            files={
                "ev_file": ("eagleview.pdf", dummy_ev, "application/pdf"),
                "sol_file": ("statement_of_loss.pdf", dummy_sol, "application/pdf")
            },
            cookies={"auth_token": admin_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert "supplement generation skipped" in data["message"]
        assert "Supplement generation enqueued" not in data["message"]
        assert retail_quote_called is True
        assert supplement_pipeline_called is False
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_upload_measurement_report_rejects_non_pdf():
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    # 1. Test wrong content-type header
    res1 = client.post(
        f"/api/office/jobs/{job_id}/measurement-report",
        files={"file": ("report.txt", io.BytesIO(b"Plain text content"), "text/plain")},
        cookies={"auth_token": admin_token}
    )
    assert res1.status_code == 400
    assert "File must be a PDF" in res1.json()["detail"]

    # 2. Test PDF content-type header with non-PDF magic bytes
    res2 = client.post(
        f"/api/office/jobs/{job_id}/measurement-report",
        files={"file": ("fake.pdf", io.BytesIO(b"NOT_A_REAL_PDF_HEADER"), "application/pdf")},
        cookies={"auth_token": admin_token}
    )
    assert res2.status_code == 400
    assert "Invalid file type" in res2.json()["detail"]


def test_upload_statement_of_loss_rejects_non_pdf():
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    # 1. Test wrong content-type header
    res1 = client.post(
        f"/api/office/jobs/{job_id}/statement-of-loss",
        files={"file": ("sol.txt", io.BytesIO(b"Plain text content"), "text/plain")},
        cookies={"auth_token": admin_token}
    )
    assert res1.status_code == 400
    assert "File must be a PDF" in res1.json()["detail"]

    # 2. Test PDF content-type header with non-PDF magic bytes
    res2 = client.post(
        f"/api/office/jobs/{job_id}/statement-of-loss",
        files={"file": ("fake_sol.pdf", io.BytesIO(b"NOT_A_REAL_PDF_HEADER"), "application/pdf")},
        cookies={"auth_token": admin_token}
    )
    assert res2.status_code == 400
    assert "Invalid file type" in res2.json()["detail"]


def test_document_intake_endpoints_reject_invalid_job_id():
    client = TestClient(app)
    admin_token = create_access_token("admin")
    malformed_job_id = "invalid-uuid-format-12345"

    dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy content")
    res1 = client.post(
        f"/api/office/jobs/{malformed_job_id}/measurement-report",
        files={"file": ("report.pdf", dummy_pdf, "application/pdf")},
        cookies={"auth_token": admin_token}
    )
    assert res1.status_code == 400
    assert "Invalid job_id format" in res1.json()["detail"]

    dummy_pdf2 = io.BytesIO(b"%PDF-1.4 dummy content")
    res2 = client.post(
        f"/api/office/jobs/{malformed_job_id}/statement-of-loss",
        files={"file": ("sol.pdf", dummy_pdf2, "application/pdf")},
        cookies={"auth_token": admin_token}
    )
    assert res2.status_code == 400
    assert "Invalid job_id format" in res2.json()["detail"]


def test_upload_statement_of_loss_idempotent_duplicate(monkeypatch):
    client = TestClient(app)
    admin_token = create_access_token("admin")
    job_id = str(uuid.uuid4())

    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Idempotent SoL Homeowner', '110 Idempotent St', 'Valdosta', 'GA', '31601', '555-0111', 'LEAD_CAPTURED', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("app.api.office_routes.detect_pdf_format", lambda p: "EAGLEVIEW")
    app.state.redis_pool = None

    try:
        content = b"%PDF-1.4 identical statement of loss content for idempotency"
        dummy1 = io.BytesIO(content)
        res1 = client.post(
            f"/api/office/jobs/{job_id}/statement-of-loss",
            files={"file": ("statement_of_loss.pdf", dummy1, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res1.status_code == 200

        dummy2 = io.BytesIO(content)
        res2 = client.post(
            f"/api/office/jobs/{job_id}/statement-of-loss",
            files={"file": ("statement_of_loss.pdf", dummy2, "application/pdf")},
            cookies={"auth_token": admin_token}
        )
        assert res2.status_code == 200
        assert "Duplicate file detected" in res2.json()["message"]
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


