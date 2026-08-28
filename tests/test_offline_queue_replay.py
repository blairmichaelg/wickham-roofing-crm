"""
Integration tests simulating client-side IndexedDB offline queue replay API sequences.
"""

import uuid
import datetime
from datetime import UTC
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.auth import create_access_token
from app.core.cache import init_db as init_cache_db
from app.core.database import run_migrations as init_crm_db
from app.core.database import get_connection

client = TestClient(app)

@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis pool to avoid background task failure."""
    app.state.redis_pool = AsyncMock()
    yield app.state.redis_pool
    if hasattr(app.state, "redis_pool"):
        delattr(app.state, "redis_pool")

@pytest.fixture(autouse=True)
def setup_dirs(tmp_path, monkeypatch):
    """Isolate storage directories and init DB tables."""
    test_field_photos = tmp_path / "field_photos"
    test_field_docs = tmp_path / "field_docs"
    test_field_photos.mkdir()
    test_field_docs.mkdir()
    
    monkeypatch.setattr("app.api.field_routes.FIELD_PHOTOS_DIR", test_field_photos)
    monkeypatch.setattr("app.api.field_routes.FIELD_DOCS_DIR", test_field_docs)
    
    init_cache_db()
    init_crm_db()
    
    # Clean databases
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


def replay_queued_record_client(client, headers, record):
    """
    Python port of the JavaScript replayQueuedRecord(record) function from field_app.html.
    Executes the exact sequence of REST requests against FastApi.
    """
    payload = record["payload"]
    job_id = payload.get("existing_job_id")
    
    if not job_id:
        resp = client.post("/api/field/jobs", headers=headers, json=payload["lead"])
        assert resp.status_code in (200, 201)
        job_id = resp.json()["job_id"]
        
    for photo_name, photo_bytes, content_type in payload.get("photos", []):
        files = {"file": (photo_name, photo_bytes, content_type)}
        resp = client.post(f"/api/field/jobs/{job_id}/photos", headers=headers, files=files)
        assert resp.status_code == 200
        
    if payload.get("signature"):
        is_retail = (payload["lead"].get("job_type") == "RETAIL")
        if is_retail:
            sig_url = f"/api/field/jobs/{job_id}/sign-retail-contract"
            sig_payload = {
                "signature_base64": payload["signature"],
                "signer_name": payload["signer_name"],
                "user_agent": "Mozilla/5.0 (Test OS)",
                "total_price": payload.get("total_price", 0),
                "deposit_amount": payload.get("deposit_amount", 0),
                "scope_description": payload.get("scope_description", "")
            }
        else:
            sig_url = f"/api/field/jobs/{job_id}/contingency-sign"
            sig_payload = {
                "signature_base64": payload["signature"],
                "signer_name": payload["signer_name"],
                "user_agent": "Mozilla/5.0 (Test OS)"
            }
            
        resp = client.post(sig_url, headers=headers, json=sig_payload)
        assert resp.status_code == 200
        
    return job_id


@pytest.mark.asyncio
async def test_offline_lead_intake_replay():
    """Verify that replaying a queued offline lead inserts it into SQLite."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    record = {
        "id": "queue-item-1",
        "payload": {
            "existing_job_id": None,
            "lead": {
                "homeowner_name": "Offline John",
                "address_line1": "123 Off Grid Way",
                "city": "Thomasville",
                "state": "GA",
                "postal_code": "31757",
                "phone": "555-9876",
                "job_type": "INSURANCE"
            }
        }
    }
    
    job_id = replay_queued_record_client(client, headers, record)
    assert job_id is not None
    
    # Query database and verify fields
    conn = get_connection()
    try:
        row = conn.execute("SELECT homeowner_name, status, job_type FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        assert row["homeowner_name"] == "Offline John"
        assert row["status"] == "LEAD_CAPTURED"
        assert row["job_type"] == "INSURANCE"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_offline_photo_upload_replay():
    """Verify that replaying a queued photo upload for an existing job works."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    # Create existing job
    create_resp = client.post("/api/field/jobs", headers=headers, json={
        "homeowner_name": "Existing Homeowner",
        "address_line1": "456 Oak Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31757",
        "phone": "555-4321"
    })
    assert create_resp.status_code == 200
    job_id = create_resp.json()["job_id"]
    
    record = {
        "id": "queue-item-2",
        "payload": {
            "existing_job_id": job_id,
            "lead": {"job_type": "INSURANCE"},
            "photos": [
                ("shingle_damage.jpg", b"\xff\xd8\xff\xe0\x00\x10JFIFfakecontent", "image/jpeg")
            ]
        }
    }
    
    with patch("app.services.ai_service.GeminiClient") as MockClient:
        # Mock Gemini response to bypass actual model calls
        mock_inst = MockClient.return_value
        mock_inst.analyze_images = patch("app.services.ai_service.GeminiClient.analyze_images")
        
        returned_job_id = replay_queued_record_client(client, headers, record)
        assert returned_job_id == job_id
        
    # Verify document registration in database
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT filename, file_type, visibility FROM job_documents WHERE job_id = ?", (job_id,))
        rows = cursor.fetchall()
        assert len(rows) >= 1
        assert any("Inspection_Photo_" in r["filename"] for r in rows)
        assert any(r["visibility"] == "field_safe" for r in rows)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_offline_contingency_signature_replay():
    """Verify that replaying a signed contingency agreement advances status and vaults PDF."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    # Create existing job
    create_resp = client.post("/api/field/jobs", headers=headers, json={
        "homeowner_name": "Signed Homeowner",
        "address_line1": "789 Pine Rd",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31757",
        "phone": "555-5555"
    })
    job_id = create_resp.json()["job_id"]
    
    # Queue item with base64 signature (contingency)
    record = {
        "id": "queue-item-3",
        "payload": {
            "existing_job_id": job_id,
            "lead": {"job_type": "INSURANCE"},
            "signature": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
            "signer_name": "Homeowner Signature"
        }
    }
    
    returned_job_id = replay_queued_record_client(client, headers, record)
    assert returned_job_id == job_id
    
    # Verify job status updated to CONTINGENCY_SIGNED
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "CONTINGENCY_SIGNED"
        
        # Verify the signed contract exists in vault
        doc_row = conn.execute("SELECT filename, category FROM job_documents WHERE job_id = ? AND category = 'CONTINGENCY_SIGNED'", (job_id,)).fetchone()
        assert doc_row is not None
        assert "Contingency_Agreement" in doc_row["filename"]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_offline_retail_signature_replay():
    """Verify that replaying a signed retail contract advances status and vaults PDF."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    # Create existing job with retail job_type
    create_resp = client.post("/api/field/jobs", headers=headers, json={
        "homeowner_name": "Retail Homeowner",
        "address_line1": "321 Cedar St",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31757",
        "phone": "555-9999",
        "job_type": "RETAIL"
    })
    job_id = create_resp.json()["job_id"]
    
    # Queue item with base64 signature (retail)
    record = {
        "id": "queue-item-4",
        "payload": {
            "existing_job_id": job_id,
            "lead": {"job_type": "RETAIL"},
            "signature": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
            "signer_name": "Retail Client",
            "total_price": 12500.0,
            "deposit_amount": 2500.0,
            "scope_description": "Full Shingle Roof Replacement"
        }
    }
    
    returned_job_id = replay_queued_record_client(client, headers, record)
    assert returned_job_id == job_id
    
    # Verify job status updated to RETAIL_CONTRACT_SIGNED
    conn = get_connection()
    try:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "RETAIL_CONTRACT_SIGNED"
        
        # Verify the signed retail contract and Notice of Cancellation exist in vault
        doc_rows = conn.execute("SELECT category FROM job_documents WHERE job_id = ?", (job_id,)).fetchall()
        categories = [r["category"] for r in doc_rows]
        assert "RETAIL_CONTRACT_SIGNED" in categories
        assert "RETAIL_NOTICE_OF_CANCELLATION" in categories
    finally:
        conn.close()


def simulate_replay_loop(client, headers, record):
    """
    Simulates the client-side IndexedDB loop in JavaScript, including retry/error handling.
    """
    payload = record["payload"]
    job_id = payload.get("existing_job_id")
    
    try:
        if not job_id:
            resp = client.post("/api/field/jobs", headers=headers, json=payload["lead"])
            if resp.status_code != 200:
                class CustomError(Exception):
                    status = resp.status_code
                raise CustomError(f"HTTP {resp.status_code}: {resp.text}")
            job_id = resp.json()["job_id"]
            
        for photo_name, photo_bytes, content_type in payload.get("photos", []):
            files = {"file": (photo_name, photo_bytes, content_type)}
            resp = client.post(f"/api/field/jobs/{job_id}/photos", headers=headers, files=files)
            if resp.status_code != 200:
                class CustomError(Exception):
                    status = resp.status_code
                raise CustomError(f"HTTP {resp.status_code}: {resp.text}")
                
        if payload.get("signature"):
            is_retail = (payload["lead"].get("job_type") == "RETAIL")
            if is_retail:
                sig_url = f"/api/field/jobs/{job_id}/sign-retail-contract"
                sig_payload = {
                    "signature_base64": payload["signature"],
                    "signer_name": payload["signer_name"],
                    "user_agent": "Mozilla/5.0 (Test OS)",
                    "total_price": payload.get("total_price", 0),
                    "deposit_amount": payload.get("deposit_amount", 0),
                    "scope_description": payload.get("scope_description", "")
                }
            else:
                sig_url = f"/api/field/jobs/{job_id}/contingency-sign"
                sig_payload = {
                    "signature_base64": payload["signature"],
                    "signer_name": payload["signer_name"],
                    "user_agent": "Mozilla/5.0 (Test OS)"
                }
                
            resp = client.post(sig_url, headers=headers, json=sig_payload)
            if resp.status_code != 200:
                class CustomError(Exception):
                    status = resp.status_code
                raise CustomError(f"HTTP {resp.status_code}: {resp.text}")
                
        record["status"] = "synced"
    except Exception as err:
        status_code = getattr(err, "status", 500)
        
        record["retry_count"] = record.get("retry_count", 0) + 1
        record["error_reason"] = str(err)
        record["last_attempt_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
        record["last_http_status"] = status_code
        
        if 400 <= status_code < 500:
            record["status"] = "failed_permanent"
        elif record["retry_count"] >= 5:
            record["status"] = "failed_permanent"
        else:
            record["status"] = "pending_sync"
            
    return job_id


@pytest.mark.asyncio
async def test_offline_replay_4xx_permanent_failure():
    """Verify that replaying a queued record with a 4xx validation error marks it failed_permanent."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    # homeowner_name is missing, which will trigger a 422 validation error
    record = {
        "id": "queue-item-4xx",
        "payload": {
            "existing_job_id": None,
            "lead": {
                "address_line1": "123 Error Lane",
                "city": "Thomasville",
                "state": "GA",
                "postal_code": "31757",
                "phone": "555-9876",
                "job_type": "INSURANCE"
            }
        }
    }
    
    simulate_replay_loop(client, headers, record)
    assert record["status"] == "failed_permanent"
    assert record["last_http_status"] == 422
    assert "homeowner_name" in record["error_reason"]
    assert record["retry_count"] == 1
    assert "last_attempt_utc" in record


@pytest.mark.asyncio
async def test_offline_replay_5xx_transient_failure():
    """Verify that replaying a queued record with a transient 5xx error keeps it pending_sync and increments retry_count."""
    token = create_access_token("field")
    headers = {"x-internal-token": token}
    
    record = {
        "id": "queue-item-5xx",
        "payload": {
            "existing_job_id": None,
            "lead": {
                "homeowner_name": "Transient Bob",
                "address_line1": "123 Transient Way",
                "city": "Thomasville",
                "state": "GA",
                "postal_code": "31757",
                "phone": "555-9876",
                "job_type": "INSURANCE"
            }
        }
    }
    
    # Mock client.post to return 500 Internal Server Error
    mock_resp = AsyncMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    
    with patch.object(client, "post", return_value=mock_resp):
        simulate_replay_loop(client, headers, record)
        
    assert record["status"] == "pending_sync"
    assert record["last_http_status"] == 500
    assert record["retry_count"] == 1
    assert "last_attempt_utc" in record

    # Let's simulate retrying up to 5 times to hit permanent failure
    for i in range(4):
        with patch.object(client, "post", return_value=mock_resp):
            simulate_replay_loop(client, headers, record)
            
    assert record["status"] == "failed_permanent"
    assert record["retry_count"] == 5
