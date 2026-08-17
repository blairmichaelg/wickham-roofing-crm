import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.core.database import create_field_rep, get_connection, pwd_context
from app.main import app

client = TestClient(app)

@pytest.fixture
def test_reps_and_jobs():
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        
        # Rep 1
        rep1_id = "test-rep-1"
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES (?, ?, ?, 1)", (rep1_id, "Rep 1", pwd_context.hash("1111")))
        
        # Rep 2
        rep2_id = "test-rep-2"
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES (?, ?, ?, 1)", (rep2_id, "Rep 2", pwd_context.hash("2222")))
        
        # Job owned by Rep 1
        job_id = "test-job-rep1"
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, canvasser_rep_id, status, address_line1, city, state, postal_code, phone) "
            "VALUES (?, ?, ?, 'LEAD_CAPTURED', '123 Test St', 'Orlando', 'FL', '32801', '555-5555')", 
            (job_id, "John Doe", rep1_id)
        )
        
        conn.execute("COMMIT")
        yield {"rep1_id": rep1_id, "rep2_id": rep2_id, "job_id": job_id}
    finally:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM jobs WHERE id = 'test-job-rep1'")
        conn.execute("DELETE FROM field_reps WHERE id IN ('test-rep-1', 'test-rep-2')")
        conn.execute("COMMIT")
        conn.close()

def test_field_rep_access_isolation(test_reps_and_jobs):
    """Task 4: Test that a field rep cannot access another rep's job."""
    # Rep 2 tries to access Rep 1's job
    token = create_access_token(role="field", rep_id=test_reps_and_jobs["rep2_id"], rep_name="Rep 2")
    client.cookies.set("auth_token", token)
    
    response = client.get(f"/api/field/jobs/{test_reps_and_jobs['job_id']}/inspection")
    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorized to access this job."}

def test_field_rep_access_success(test_reps_and_jobs):
    """Task 4: Test that a field rep can access their own job."""
    # Rep 1 tries to access Rep 1's job
    token = create_access_token(role="field", rep_id=test_reps_and_jobs["rep1_id"], rep_name="Rep 1")
    client.cookies.set("auth_token", token)
    
    response = client.get(f"/api/field/jobs/{test_reps_and_jobs['job_id']}/inspection")
    # Should not be 403. Might be 404 if data missing, or 200.
    assert response.status_code != 403

def test_field_rep_pin_hashing():
    """Task 4: Test that field rep PINs are hashed in the database."""
    try:
        rep = create_field_rep("Hash Test Rep", "9876")
        conn = get_connection()
        row = conn.execute("SELECT pin_hash FROM field_reps WHERE id = ?", (rep["id"],)).fetchone()
        assert row is not None
        assert row["pin_hash"] != "9876"
        assert pwd_context.verify("9876", row["pin_hash"])
    finally:
        conn.execute("DELETE FROM field_reps WHERE id = ?", (rep["id"],))
        conn.commit()
        conn.close()
