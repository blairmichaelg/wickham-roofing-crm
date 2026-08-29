"""
Unit and Integration Tests for Georgia Statutory Compliance (Sprint 1).

Covers:
1. O.C.G.A. § 10-1-393.12:
   - 5-business-day post-denial invoicing lock and emergency exemption.
   - Detachable Notice of Cancellation and 10pt bold disclosure.
2. Georgia SB 201 (O.C.G.A. § 33-24-59.28):
   - Assignment of Benefits (AOB) detection and prevention.
3. Soft-delete and 7-year statutory retention.
"""

import datetime
import json
import sqlite3
import uuid
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import (
    get_connection,
    get_job_document_by_hash,
    get_job_documents,
    insert_job_document,
    soft_delete_job,
    soft_delete_job_agreement,
    soft_delete_job_document,
    update_job_status,
)
from app.main import app
from app.services.compliance import (
    calculate_business_days_deadline,
    detect_aob_language,
    is_post_denial_invoicing_locked,
    validate_no_aob_language,
)
from app.services.pdf.documents import DocumentsGenerator


# --- 1. AOB Language Detection Tests ---
def test_aob_detection_flags_prohibited_language():
    prohibited_samples = [
        "Homeowner hereby assigns all insurance benefits to Wickham Roofing LLC.",
        "Homeowner agrees to an assignment of benefits under the insurance policy.",
        "Contractor is authorized to receive direct payment of insurance proceeds to contractor.",
        "Homeowner assigns to contractor all insurance rights and proceeds.",
        "Customer authorizes direct payment of insurance benefits to contractor.",
        "Homeowner transfers all insurance rights to contractor.",
    ]
    for sample in prohibited_samples:
        matches = detect_aob_language(sample)
        assert len(matches) > 0, f"Failed to detect AOB in: {sample}"
        with pytest.raises(HTTPException) as exc_info:
            validate_no_aob_language(sample, is_insurance_job=True)
        assert exc_info.value.status_code == 400
        assert "Georgia SB 201" in exc_info.value.detail


def test_aob_detection_passes_clean_contract_language():
    clean_samples = [
        "Contractor agrees to perform roof replacement per insurance estimate.",
        "Homeowner is responsible for paying deductible in full per Georgia law.",
        "Homeowner authorizes contractor to inspect property and verify damage.",
        "Final contract price shall be determined by insurance carrier approved estimate.",
    ]
    for sample in clean_samples:
        matches = detect_aob_language(sample)
        assert len(matches) == 0
        # Should not raise
        validate_no_aob_language(sample, is_insurance_job=True)


# --- 2. Business Days Calculation & 5-Day Invoicing Lock Tests ---
def test_calculate_business_days_deadline():
    # Thursday -> 5 business days -> next Thursday
    thursday = datetime.datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    deadline = calculate_business_days_deadline(thursday, business_days=5)
    assert deadline == datetime.datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

    # Friday -> 5 business days -> next Friday
    friday = datetime.datetime(2026, 8, 21, 15, 0, tzinfo=timezone.utc)
    deadline_fri = calculate_business_days_deadline(friday, business_days=5)
    assert deadline_fri == datetime.datetime(2026, 8, 28, 15, 0, tzinfo=timezone.utc)


def test_post_denial_invoicing_lock(monkeypatch):
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        denial_time = datetime.datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc) # Monday
        history = [
            {"status": "LEAD_CAPTURED", "timestamp": "2026-08-20T10:00:00Z", "note": "New lead"},
            {"status": "CLAIM_DENIED", "timestamp": denial_time.isoformat(), "note": "Carrier denied"},
        ]
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type, status_history) 
            VALUES (?, 'Jane Doe', '123 Main St', 'Thomasville', 'GA', '31792', '229-555-0100', 'CLAIM_DENIED', 'insurance', ?)""",
            (job_id, json.dumps(history))
        )
        conn.commit()
    finally:
        conn.close()

    # 1. 2 business days later (Wednesday) -> Lock ACTIVE
    wednesday = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    locked, msg, unlock_dt = is_post_denial_invoicing_locked(job_id, is_emergency=False, current_time=wednesday)
    assert locked is True
    assert "Invoicing locked" in msg
    assert "O.C.G.A. § 10-1-393.12" in msg

    # 2. Emergency line item on same Wednesday -> Lock BYPASSED
    locked_emerg, _, _ = is_post_denial_invoicing_locked(job_id, is_emergency=True, current_time=wednesday)
    assert locked_emerg is False

    # 3. 6 business days later (next Tuesday) -> Lock EXPIRED
    next_tuesday = datetime.datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    locked_after, _, _ = is_post_denial_invoicing_locked(job_id, is_emergency=False, current_time=next_tuesday)
    assert locked_after is False


def test_post_denial_lock_does_not_apply_to_retail():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        denial_time = datetime.datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        history = [
            {"status": "LEAD_CAPTURED", "timestamp": "2026-08-20T10:00:00Z", "note": "New lead"},
            {"status": "CLAIM_DENIED", "timestamp": denial_time.isoformat(), "note": "Denied"},
        ]
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type, status_history) 
            VALUES (?, 'Bob Retail', '456 Oak St', 'Thomasville', 'GA', '31792', '229-555-0101', 'CLAIM_DENIED', 'retail', ?)""",
            (job_id, json.dumps(history))
        )
        conn.commit()
    finally:
        conn.close()

    locked, msg, _ = is_post_denial_invoicing_locked(job_id, is_emergency=False)
    assert locked is False


# --- 3. Soft-Delete and 7-Year Retention Tests ---
def test_soft_delete_job_document():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) 
            VALUES (?, 'Test Homeowner', '789 Pine Rd', 'Thomasville', 'GA', '31792', '229-555-0102', 'LEAD_CAPTURED')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    # Insert document
    doc_id = insert_job_document(
        job_id=job_id,
        filename="contract_v1.pdf",
        file_type="PDF",
        storage_path="/tmp/contract_v1.pdf",
        sha256_hash="dummyhash123",
        replace_existing=False
    )

    # Verify active document is returned
    docs = get_job_documents(job_id)
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id
    assert docs[0]["deleted_at"] is None

    # Soft delete document
    deleted = soft_delete_job_document(doc_id)
    assert deleted is True

    # Confirm excluded from standard active queries
    docs_active = get_job_documents(job_id, include_deleted=False)
    assert len(docs_active) == 0

    # Confirm row remains in database for 7-year audit retention
    docs_all = get_job_documents(job_id, include_deleted=True)
    assert len(docs_all) == 1
    assert docs_all[0]["id"] == doc_id
    assert docs_all[0]["deleted_at"] is not None


def test_insert_job_document_replace_existing_soft_deletes():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status) 
            VALUES (?, 'Test Homeowner 2', '101 Cedar Ln', 'Thomasville', 'GA', '31792', '229-555-0103', 'LEAD_CAPTURED')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    # Insert v1
    doc_id_1 = insert_job_document(
        job_id=job_id,
        filename="estimate.pdf",
        file_type="PDF",
        storage_path="/tmp/estimate_v1.pdf",
        category="ESTIMATE",
        replace_existing=False
    )

    # Insert v2 with replace_existing=True
    doc_id_2 = insert_job_document(
        job_id=job_id,
        filename="estimate.pdf",
        file_type="PDF",
        storage_path="/tmp/estimate_v2.pdf",
        category="ESTIMATE",
        replace_existing=True
    )

    # Only v2 is active
    active_docs = get_job_documents(job_id, include_deleted=False)
    assert len(active_docs) == 1
    assert active_docs[0]["id"] == doc_id_2

    # Both records retained in table (v1 has deleted_at set)
    all_docs = get_job_documents(job_id, include_deleted=True)
    assert len(all_docs) == 2
    deleted_items = [d for d in all_docs if d["deleted_at"] is not None]
    assert len(deleted_items) == 1
    assert deleted_items[0]["id"] == doc_id_1


# --- 4. Notice of Cancellation PDF Formatting Tests ---
@pytest.mark.asyncio
async def test_contingency_and_notice_of_cancellation_pdf_generation(tmp_path):
    job = {
        "id": str(uuid.uuid4()),
        "homeowner_name": "Georgia Resident",
        "address_line1": "456 Magnolia Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31792",
        "insurance_carrier": "State Farm",
        "claim_number": "CLM-GA-9921",
    }
    generator = DocumentsGenerator()

    # Test Notice of Cancellation generation
    noc_path = await generator.generate_notice_of_cancellation(job)
    assert Path(noc_path).exists()
    assert Path(noc_path).stat().st_size > 0

    # Test Unsigned Contingency Agreement generation
    contingency_path = await generator.generate_contingency_agreement(job)
    assert Path(contingency_path).exists()
    assert Path(contingency_path).stat().st_size > 0


@pytest.mark.asyncio
async def test_invoice_statutory_compliance_and_post_denial_lock(tmp_path, monkeypatch):
    import pdfplumber
    from app.services.pdf.invoice import InvoiceGenerator
    
    monkeypatch.setattr("app.services.pdf.invoice.FIELD_DOCS_DIR", tmp_path)
    
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "homeowner_name": "Valdosta Homeowner",
        "address_line1": "100 Peacock Way",
        "city": "Valdosta",
        "state": "GA",
        "postal_code": "31602",
        "claim_number": "CLM-INV-7711",
    }
    
    generator = InvoiceGenerator()
    
    # 1. Generate estimate PDF
    est_data = {
        "materials": ["field_shingle_bundles: 30", "drip_edge_pieces: 10"],
        "total_cost": 8500.0,
    }
    est_path = await generator.generate_estimate_pdf(est_data, job_id)
    assert Path(est_path).exists()
    
    with pdfplumber.open(est_path) as pdf:
        est_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "33-24-59.27" in est_text
        assert "HB 423" in est_text
        assert "33-1-9" not in est_text
        assert len(detect_aob_language(est_text)) == 0

    # 2. Generate final invoice PDF (unlocked)
    inv_data = {
        "invoice_number": "INV-7711",
        "items": [{"description": "Full Roof Replacement per Approved Scope", "amount": 8500.0}],
        "deductible_amount": 1000.0,
        "payments_applied": 7500.0,
    }
    inv_path = await generator.generate_final_invoice(job, inv_data)
    assert Path(inv_path).exists()
    
    with pdfplumber.open(inv_path) as pdf:
        inv_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "33-24-59.27" in inv_text
        assert "HB 423" in inv_text
        assert "33-1-9" not in inv_text
        assert len(detect_aob_language(inv_text)) == 0

    # 3. Test post-denial invoicing lock enforcement
    denial_job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        denial_time = datetime.datetime.now(timezone.utc)
        history = [
            {"status": "LEAD_CAPTURED", "timestamp": "2026-08-20T10:00:00Z", "note": "New lead"},
            {"status": "CLAIM_DENIED", "timestamp": denial_time.isoformat(), "note": "Carrier denied"},
        ]
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type, status_history)
               VALUES (?, 'Locked Lead', '100 Peacock Way', 'Valdosta', 'GA', '31602', '229-555-0199', 'CLAIM_DENIED', 'insurance', ?)""",
            (denial_job_id, json.dumps(history))
        )
        conn.commit()
    finally:
        conn.close()

    denial_job = dict(job)
    denial_job["id"] = denial_job_id
    with pytest.raises(ValueError) as exc_info:
        await generator.generate_final_invoice(denial_job, inv_data)
    assert "post-denial invoicing lock" in str(exc_info.value)
    assert "10-1-393.12" in str(exc_info.value)

