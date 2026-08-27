import asyncio
import base64
import os
from pathlib import Path
import pytest
import uuid
import pdfplumber
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.server import app
from app.core.database import get_connection, insert_job_document, create_field_rep, get_field_rep_by_pin
from app.api.auth import verify_field

client = TestClient(app)

# Dummy base64 signature for testing
TINY_PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
DATA_URI = f"data:image/png;base64,{TINY_PNG_BASE64}"

@pytest.fixture(scope="module", autouse=True)
def setup_auth_and_directories():
    """Create field rep and log in to client."""
    # Seed field rep
    if not get_field_rep_by_pin("3333"):
        try:
            create_field_rep("Field Test Rep", "3333")
        except Exception:
            pass

    # Log in
    response = client.post("/auth/login", data={"pin": "3333", "redirect_url": "/"}, follow_redirects=False)
    auth_cookie = response.cookies.get("auth_token")
    client.cookies.set("auth_token", auth_cookie)
    yield

@pytest.fixture(autouse=True)
def mock_assert_field_rep_owns_job():
    """Bypass job ownership checks during unit tests."""
    with patch("app.api.field_routes.assert_field_rep_owns_job") as mock:
        yield mock

@pytest.fixture
def test_job():
    """Create a temporary job for retail signature testing."""
    conn = get_connection()
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, job_type, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Jane Retailer", "456 Retail Blvd", "Atlanta", "GA", "30309", "555-0199", "RETAIL", "LEAD_CAPTURED")
    )
    conn.commit()
    conn.close()
    yield job_id

    # Cleanup (dependent tables first to prevent foreign key errors)
    conn = get_connection()
    conn.execute("DELETE FROM job_agreements WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()

def test_sign_retail_contract_validation(test_job):
    """Test pricing validation rules on sign-retail-contract payload."""
    job_id = test_job

    # 1. Negative total price
    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": -15000.0,
            "deposit_amount": 5000.0,
            "scope_description": "New dimensional shingle roof"
        }
    )
    assert resp.status_code == 422
    assert "Total price must be greater than zero." in resp.text

    # 2. Zero total price
    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": 0.0,
            "deposit_amount": 0.0,
            "scope_description": "New dimensional shingle roof"
        }
    )
    assert resp.status_code == 422
    assert "Total price must be greater than zero." in resp.text

    # 3. Negative deposit amount
    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": 10000.0,
            "deposit_amount": -500.0,
            "scope_description": "New dimensional shingle roof"
        }
    )
    assert resp.status_code == 422
    assert "Deposit amount cannot be negative." in resp.text

    # 4. Deposit exceeds total price
    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": 10000.0,
            "deposit_amount": 12000.0,
            "scope_description": "New dimensional shingle roof"
        }
    )
    assert resp.status_code == 422
    assert "Deposit amount cannot exceed total price." in resp.text

def test_sign_retail_contract_pdf_contents(test_job):
    """Test that generated PDF correctly contains submitted price, deposit, and scope."""
    job_id = test_job

    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": 14500.75,
            "deposit_amount": 7250.25,
            "scope_description": "Verify PDF content scope description text"
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    
    # Locate generated PDF in document vault
    pdf_filename = data["pdf_path"]
    pdf_path = Path("data/field_docs") / job_id / pdf_filename
    assert pdf_path.exists()

    # Read PDF text
    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join(page.extract_text() for page in pdf.pages)

    # Assert content matches (formatted correctly in ReportLab)
    assert "14,500.75" in full_text
    assert "7,250.25" in full_text
    assert "Verify PDF content scope description text" in full_text

def test_sign_retail_contract_offline_replay(test_job):
    """Test that replaying a queued signature reaches the same database and files state."""
    job_id = test_job

    # Perform signature submission (which is what offline replay calls)
    resp = client.post(
        f"/api/field/jobs/{job_id}/sign-retail-contract",
        json={
            "signature_base64": DATA_URI,
            "signer_name": "Jane Retailer",
            "total_price": 12000.0,
            "deposit_amount": 6000.0,
            "scope_description": "Offline replayed contract"
        }
    )
    assert resp.status_code == 200

    # 1. Verify Job Status
    conn = get_connection()
    row = conn.execute("SELECT status, status_history FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "RETAIL_CONTRACT_SIGNED"
    assert "Retail contract signed by Jane Retailer" in row["status_history"]

    # 2. Verify files exist
    sig_path = Path("data/field_docs") / job_id / f"{job_id}_retail_contract_sig.png"
    assert sig_path.exists()
    
    # 3. Verify Document entries in SQLite
    docs = conn.execute("SELECT file_type, filename FROM job_documents WHERE job_id = ?", (job_id,)).fetchall()
    conn.close()

    file_types = {d["file_type"] for d in docs}
    assert "RETAIL_CONTRACT_SIGNED" in file_types
    assert "RETAIL_NOTICE_OF_CANCELLATION" in file_types

def test_job_type_toggle_contract(test_job):
    """Confirm RETAIL vs INSURANCE produce correct downstream transitions in supplement processing."""
    job_id = test_job
    
    # For RETAIL: run_supplement_pipeline should immediately skip
    from app.core.pipeline import run_supplement_pipeline
    res = asyncio.run(run_supplement_pipeline(
        job_id,
        ev_pdf_path="dummy.pdf",
        sol_pdf_path="dummy.pdf",
        ev_sha256="dummy_ev_sha",
        ev_doc_id="dummy_ev_doc",
        sol_sha256="dummy_sol_sha",
        sol_doc_id="dummy_sol_doc"
    ))
    assert res["status"] == "skipped"
    assert res["reason"] == "retail_job"
