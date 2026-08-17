import uuid

import pytest

from app.core.database import (
    JobStatus,
    force_override_status,
    get_connection,
    get_job_documents,
    insert_job_document,
    update_job_status,
)


@pytest.fixture
def setup_job():
    job_id = f"test_job_{uuid.uuid4().hex[:6]}"
    conn = get_connection()
    conn.execute('''
        INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status)
        VALUES (?, 'Test Owner', '123 Test St', 'Test City', 'GA', '30303', '555-0100', ?)
    ''', (job_id, JobStatus.LEAD_CAPTURED.value))
    
    conn.execute('''
        INSERT INTO financials (job_id, revenue_cents, carrier_rcv_cents, material_cost_cents, labor_cost_cents, overhead_pct, canvasser_commission_pct, permits_fee_cents)
        VALUES (?, 10000, 10000, 3000, 2000, 10, 5, 100)
    ''', (job_id,))
    conn.close()
    return job_id

def test_install_blocked_without_materials_on_site(setup_job):
    job_id = setup_job
    update_job_status(job_id, JobStatus.MATERIAL_ORDERED)
    
    with pytest.raises(RuntimeError, match="Cannot schedule install until MATERIALS_ON_SITE is confirmed"):
        update_job_status(job_id, JobStatus.INSTALL_SCHEDULED)

def test_install_allowed_with_materials_on_site(setup_job):
    job_id = setup_job
    update_job_status(job_id, JobStatus.MATERIAL_ORDERED)
    update_job_status(job_id, JobStatus.MATERIALS_ON_SITE)
    
    # This should not raise an error
    update_job_status(job_id, JobStatus.INSTALL_SCHEDULED)
    
    conn = get_connection()
    cursor = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,))
    assert cursor.fetchone()["status"] == JobStatus.INSTALL_SCHEDULED
    conn.close()

def test_document_versioning_appends_not_overwrites(setup_job):
    job_id = setup_job
    insert_job_document(job_id, "estimate.pdf", "ESTIMATE", "/path/v1.pdf", "hash1")
    insert_job_document(job_id, "estimate.pdf", "ESTIMATE", "/path/v2.pdf", "hash2")
    
    docs = get_job_documents(job_id, "ESTIMATE")
    assert len(docs) == 2
    hashes = {d["sha256_hash"] for d in docs}
    assert hashes == {"hash1", "hash2"}

def test_operator_gate_classification():
    assert JobStatus.is_operator_gate(JobStatus.SUPPLEMENT_GENERATED) is True
    assert JobStatus.is_operator_gate(JobStatus.PENDING_OPERATOR_REVIEW) is False

def test_force_override_status(setup_job):
    job_id = setup_job
    # Usually this transition would be illegal without financials etc
    force_override_status(job_id, JobStatus.CLOSED, "Admin testing override")
    
    conn = get_connection()
    cursor = conn.execute("SELECT status, status_history FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    
    assert row["status"] == JobStatus.CLOSED
    assert "ADMIN OVERRIDE: Admin testing override" in row["status_history"]
