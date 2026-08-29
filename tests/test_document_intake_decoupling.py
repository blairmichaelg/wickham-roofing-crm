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
