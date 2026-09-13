"""
tests/test_revenue_capture_and_evidence_matrix.py — Tests for Storm Opportunities,
Next Best Actions, Evidence Matrix v1, and Supplement Evidence Packet generation.
"""

import os
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path
import pytest

from app.core.database import run_migrations, get_db_connection, get_connection
from app.services.storm_matching import (
    assign_storm_opportunity,
    get_storm_metrics,
    get_storm_opportunities,
    log_contact_attempt,
    match_storm_opportunities,
    update_storm_opportunity_status,
)
from app.services.next_best_action import (
    evaluate_job_next_actions,
    get_field_best_actions,
    get_office_action_triage,
)
from app.services.evidence_matrix import (
    create_evidence_exhibit,
    get_job_evidence_exhibits,
    reorder_evidence_exhibits,
    update_evidence_exhibit,
)
from app.services.pdf.evidence_packet import EvidencePacketGenerator
from fastapi.testclient import TestClient
from app.main import app
from app.api.auth import create_access_token



@pytest.fixture
def temp_db():
    """Create a temporary SQLite database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name
    run_migrations(db_path)
    yield db_path
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


def _seed_job(db_path: str, job_id: str, status: str = "LEAD_CAPTURED", postal_code: str = "31792", rep_id: str = "rep_1") -> None:
    with get_db_connection(db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES (?, ?, 'hash_' || ?, 1)", (rep_id, f"Rep {rep_id}", rep_id))
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES ('rep_sarah', 'Sarah', 'hash_sarah', 1)")
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES ('rep_mike', 'Mike', 'hash_mike', 1)")
        conn.execute(
            """
            INSERT INTO jobs (id, homeowner_name, phone, address_line1, city, state, postal_code, status, canvasser_name, canvasser_rep_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (job_id, f"Homeowner {job_id[:4]}", "229-555-0199", "123 Oak St", "Thomasville", "GA", postal_code, status, rep_id, rep_id),
        )
        conn.commit()


def _seed_storm(db_path: str, event_id: str, event_type: str = "HAIL", hail_size: float = 1.5, wind_speed: float = 0.0, zip_code: str = "31792") -> None:
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO storm_events (id, zipcode, event_type, event_date, hail_size_inches, wind_speed_mph, source)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, 'NWS')
            """,
            (event_id, zip_code, event_type, hail_size, wind_speed),
        )
        conn.commit()


# --- Storm Opportunities & Matching Tests ---

def test_idempotent_storm_matching(temp_db):
    """Verify storm matching finds eligible jobs and never creates duplicate opportunity rows."""
    _seed_job(temp_db, "job_active_1", status="LEAD_CAPTURED", postal_code="31792")
    _seed_job(temp_db, "job_terminal_2", status="CLOSED", postal_code="31792")  # should be excluded
    _seed_storm(temp_db, "storm_1", event_type="HAIL", hail_size=1.75, zip_code="31792")

    with get_db_connection(temp_db) as conn:
        res1 = match_storm_opportunities(conn, lookback_hours=168)
        assert res1["opportunities_matched"] == 1

        # Re-running immediately must be idempotent (0 new created)
        res2 = match_storm_opportunities(conn, lookback_hours=168)
        assert res2["opportunities_matched"] == 0

    opps = get_storm_opportunities(db_path=temp_db)
    assert len(opps) == 1
    assert opps[0]["job_id"] == "job_active_1"
    assert '1.75" Hail' in opps[0]["severity_summary"]


def test_storm_opportunity_lifecycle_and_contact_log(temp_db):
    """Test assigning, status updating, logging contact attempts, and computing storm metrics."""
    _seed_job(temp_db, "job_lifecycle", postal_code="31792")
    _seed_storm(temp_db, "storm_2", event_type="WIND", wind_speed=65.0, zip_code="31792")

    with get_db_connection(temp_db) as conn:
        match_storm_opportunities(conn, lookback_hours=168)

    opps = get_storm_opportunities(db_path=temp_db)
    opp_id = opps[0]["id"]

    # Assign to rep
    assign_storm_opportunity(opp_id, "rep_sarah", db_path=temp_db)
    updated_opps = get_storm_opportunities(rep_id="rep_sarah", db_path=temp_db)
    assert len(updated_opps) == 1

    # Log contact attempt
    attempt = log_contact_attempt(
        job_id="job_lifecycle",
        rep_id="rep_sarah",
        contact_method="CALL",
        outcome="spoke_to_homeowner",
        notes="Homeowner requested roof inspection next Tuesday",
        opportunity_id=opp_id,
        db_path=temp_db,
    )
    assert attempt["contact_method"] == "CALL"

    # Update status to inspection_scheduled
    update_storm_opportunity_status(opp_id, "inspection_scheduled", db_path=temp_db)

    # Verify metrics
    metrics = get_storm_metrics(db_path=temp_db)
    assert metrics["surfaced"] == 1
    assert metrics["contacted"] == 1
    assert metrics["inspections_scheduled"] == 1


# --- Next Best Action Tests ---

def test_deterministic_next_best_actions(temp_db):
    """Verify next best actions follow deterministic business rules for core job statuses."""
    _seed_job(temp_db, "job_intake", status="LEAD_CAPTURED", rep_id="rep_mike")
    _seed_job(temp_db, "job_contingency", status="CONTINGENCY_SIGNED", rep_id="rep_mike")

    actions_mike = get_field_best_actions(rep_id="rep_mike", db_path=temp_db)
    action_titles = [a["action_title"] for a in actions_mike]
    assert any("Agreement" in t or "Intake" in t for t in action_titles)
    assert any("Inspection" in t or "Photos" in t for t in action_titles)

    # Office triage grouping
    triage = get_office_action_triage(db_path=temp_db)
    assert "stalled_jobs" in triage
    assert "unassigned_storm_opportunities" in triage
    assert "supplement_packets_awaiting_review" in triage
    assert "missing_production_artifacts" in triage


# --- Evidence Matrix v1 Tests ---

def test_evidence_matrix_crud_and_reordering(temp_db):
    """Verify creating exhibits, sequential numbering, updating status, and reordering."""
    _seed_job(temp_db, "job_matrix", postal_code="31792")
    ex1 = create_evidence_exhibit(
        job_id="job_matrix",
        category="decking",
        observation="Rotted decking near chimney counter flashing",
        location_roof_area="North slope, valley",
        db_path=temp_db,
    )
    assert ex1["exhibit_number"] == 1
    assert ex1["category"] == "decking_sheathing"

    ex2 = create_evidence_exhibit(
        job_id="job_matrix",
        category="flashing",
        observation="Corroded step flashing requiring replacement",
        db_path=temp_db,
    )
    assert ex2["exhibit_number"] == 2

    # Update review status
    updated = update_evidence_exhibit(
        exhibit_id=ex1["id"],
        status="included",
        requires_office_review=False,
        db_path=temp_db,
    )
    assert updated["status"] == "included"
    assert updated["requires_office_review"] is False

    # Reorder exhibits
    reorder_evidence_exhibits("job_matrix", [ex2["id"], ex1["id"]], db_path=temp_db)
    all_ex = get_job_evidence_exhibits("job_matrix", db_path=temp_db)
    assert all_ex[0]["id"] == ex2["id"]
    assert all_ex[0]["exhibit_number"] == 1
    assert all_ex[1]["id"] == ex1["id"]
    assert all_ex[1]["exhibit_number"] == 2


def test_evidence_packet_pdf_generation(temp_db, tmp_path, monkeypatch):
    """Verify PDF generator runs synchronously, builds the document, and does not crash."""
    monkeypatch.setattr("app.services.pdf.evidence_packet.FIELD_DOCS_DIR", tmp_path)
    _seed_job(temp_db, "job_pdf_test", postal_code="31792")
    create_evidence_exhibit(
        job_id="job_pdf_test",
        category="ventilation",
        observation="Damaged ridge vent with collapsed baffle",
        location_roof_area="Main Ridge",
        db_path=temp_db,
    )

    gen = EvidencePacketGenerator()
    pdf_path = gen.generate_packet_sync("job_pdf_test", version=1, db_path=temp_db)

    assert os.path.exists(pdf_path)
    assert pdf_path.endswith(".pdf")
    assert os.path.getsize(pdf_path) > 1000


def test_storm_opportunity_dedup_semantics(temp_db):
    """
    Reconcile and assert deduplication semantics:
    1. Same (job_id, storm_event_id) is blocked by UNIQUE constraint (idempotent re-run).
    2. A different storm_event_id hitting the same job creates a NEW opportunity.
    """
    _seed_job(temp_db, "job_dedup_test", postal_code="31792")
    _seed_storm(temp_db, "storm_ev_1", event_type="HAIL", hail_size=1.5, zip_code="31792")

    with get_db_connection(temp_db) as conn:
        res1 = match_storm_opportunities(conn, lookback_hours=168)
        assert res1["opportunities_matched"] == 1

        # Re-running against same storm event is 100% idempotent
        res2 = match_storm_opportunities(conn, lookback_hours=168)
        assert res2["opportunities_matched"] == 0

    opps = get_storm_opportunities(job_id="job_dedup_test", db_path=temp_db)
    assert len(opps) == 1
    assert opps[0]["storm_event_id"] == "storm_ev_1"

    # A second distinct qualifying event hits the same area
    _seed_storm(temp_db, "storm_ev_2", event_type="WIND", wind_speed=60.0, zip_code="31792")
    with get_db_connection(temp_db) as conn:
        res3 = match_storm_opportunities(conn, lookback_hours=168)
        assert res3["opportunities_matched"] == 1

    opps_after = get_storm_opportunities(job_id="job_dedup_test", db_path=temp_db)
    assert len(opps_after) == 2
    event_ids = {o["storm_event_id"] for o in opps_after}
    assert event_ids == {"storm_ev_1", "storm_ev_2"}


def test_next_best_action_full_status_coverage(temp_db):
    """Verify next best actions across all priority branches and statuses."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    # 1. Statutory Denial Lock (Priority 1)
    denied_job = {"id": "j_denied", "homeowner_name": "Alice", "address_line1": "101 Main", "status": "CLAIM_DENIED"}
    actions_denied = evaluate_job_next_actions(denied_job)
    assert any(a["priority"] == 1 and "Denial Lock" in a["title"] for a in actions_denied)

    # 2. Commercial Statutory Lien Warning (Priority 1)
    comm_job = {
        "id": "j_comm",
        "homeowner_name": "Commercial Plaza",
        "address_line1": "202 Center",
        "status": "APPROVED",
        "job_type": "COMMERCIAL",
        "last_work_date": (now - timedelta(days=70)).strftime("%Y-%m-%d"),
    }
    actions_comm = evaluate_job_next_actions(comm_job)
    assert any(a["priority"] == 1 and "Lien Deadline" in a["title"] for a in actions_comm)

    # 3. Payment received without closed ledger (Priority 1)
    pay_job = {"id": "j_pay", "homeowner_name": "Bob", "address_line1": "303 Oak", "status": "PAYMENT_RECEIVED"}
    actions_pay = evaluate_job_next_actions(pay_job)
    assert any(a["priority"] == 1 and "Close Ledger" in a["title"] for a in actions_pay)

    # 4. Pending operator review (Priority 2)
    review_job = {"id": "j_review", "homeowner_name": "Charlie", "address_line1": "404 Elm", "status": "PENDING_OPERATOR_REVIEW"}
    actions_review = evaluate_job_next_actions(review_job)
    assert any(a["priority"] == 2 and "Office Triage" in a["title"] for a in actions_review)

    # 5. Awaiting carrier response with SLA exceeded (Priority 2)
    sla_job = {
        "id": "j_sla",
        "homeowner_name": "David",
        "address_line1": "505 Birch",
        "status": "AWAITING_CARRIER_RESPONSE",
        "supplement_sent_at": (now - timedelta(days=20)).isoformat(),
        "carrier_sla_days": 14,
    }
    actions_sla = evaluate_job_next_actions(sla_job)
    assert any(a["priority"] == 2 and "Carrier SLA Exceeded" in a["title"] for a in actions_sla)

    # 6. Active storm opportunities in evaluate_job_next_actions (Priority 3)
    opp = {"id": "opp_1", "job_id": "j_storm", "status": "new", "severity_summary": "1.75\" Hail", "matched_date": "2026-09-11"}
    storm_job = {"id": "j_storm", "homeowner_name": "Eve", "address_line1": "606 Cedar", "status": "LEAD_CAPTURED"}
    actions_storm = evaluate_job_next_actions(storm_job, active_storm_opps=[opp])
    assert any(a["priority"] == 3 and "Storm Outreach" in a["title"] for a in actions_storm)

    # 7. Production lifecycle states (Priority 5)
    photos_job = {"id": "j_p1", "homeowner_name": "Frank", "status": "PHOTOS_UPLOADED"}
    assert any("Upload EagleView" in a["title"] for a in evaluate_job_next_actions(photos_job))

    supp_gen_job = {"id": "j_p2", "homeowner_name": "Grace", "status": "SUPPLEMENT_GENERATED"}
    assert any("Submit Supplement" in a["title"] for a in evaluate_job_next_actions(supp_gen_job))

    supp_app_job = {"id": "j_p3", "homeowner_name": "Henry", "status": "SUPPLEMENT_APPROVED"}
    assert any("Generate Supplier PO" in a["title"] for a in evaluate_job_next_actions(supp_app_job))

    installed_job = {"id": "j_p4", "homeowner_name": "Ivy", "status": "INSTALL_COMPLETED"}
    actions_inst = evaluate_job_next_actions(installed_job)
    assert any("Final Punch List" in a["title"] for a in actions_inst)
    assert any("review" in (a.get("suggested_script") or "").lower() for a in actions_inst)


def test_field_and_office_evidence_api_endpoints(temp_db, tmp_path, monkeypatch):
    """Test all field and office evidence API endpoints including RBAC and error paths."""
    monkeypatch.setattr("app.core.database.get_db_path", lambda: Path(temp_db))
    monkeypatch.setattr("app.services.pdf.evidence_packet.FIELD_DOCS_DIR", tmp_path)
    monkeypatch.setattr("app.api.office.evidence.FIELD_DOCS_DIR", tmp_path)

    _seed_job(temp_db, "job_api_evidence_1", status="LEAD_CAPTURED", postal_code="31792", rep_id="rep_api_1")
    with get_db_connection(temp_db) as conn:
        conn.execute("INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES ('rep_api_2', 'Rep Two', 'hash2', 1)")
        conn.commit()

    client = TestClient(app)
    rep1_token = create_access_token(role="field", rep_id="rep_api_1", rep_name="Rep One")
    rep2_token = create_access_token(role="field", rep_id="rep_api_2", rep_name="Rep Two")
    admin_token = create_access_token(role="admin")
    REP1_HEADERS = {"x-internal-token": rep1_token}
    REP2_HEADERS = {"x-internal-token": rep2_token}
    ADMIN_HEADERS = {"x-internal-token": admin_token}

    # 1. Field creates evidence on owned job
    res = client.post(
        "/api/field/jobs/job_api_evidence_1/evidence",
        json={"category": "decking", "observation": "Spaced 1x6 decking exceeding 1/4 inch gap"},
        headers=REP1_HEADERS,
    )
    assert res.status_code == 200, res.text
    exhibit_id = res.json()["exhibit"]["id"]

    # 2. Field lists evidence on owned job
    res = client.get("/api/field/jobs/job_api_evidence_1/evidence", headers=REP1_HEADERS)
    assert res.status_code == 200
    assert res.json()["count"] >= 1

    # 3. Field Rep 2 blocked from accessing Rep 1's job (403 IDOR check)
    res = client.get("/api/field/jobs/job_api_evidence_1/evidence", headers=REP2_HEADERS)
    assert res.status_code == 403

    # 4. Office lists evidence
    res = client.get("/api/office/jobs/job_api_evidence_1/evidence", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert res.json()["count"] >= 1

    # 5. Office creates evidence
    res = client.post(
        "/api/office/jobs/job_api_evidence_1/evidence",
        json={"category": "flashing", "observation": "Rusted step flashing", "status": "reviewed"},
        headers=ADMIN_HEADERS,
    )
    assert res.status_code == 200

    # 6. Office updates evidence
    res = client.patch(
        f"/api/office/jobs/job_api_evidence_1/evidence/{exhibit_id}",
        json={"status": "included", "requires_office_review": False},
        headers=ADMIN_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["exhibit"]["status"] == "included"

    # 7. Office updates nonexistent exhibit (404)
    res = client.patch(
        "/api/office/jobs/job_api_evidence_1/evidence/nonexistent-id",
        json={"status": "included"},
        headers=ADMIN_HEADERS,
    )
    assert res.status_code == 404

    # 8. Office reorders exhibits
    res = client.post(
        "/api/office/jobs/job_api_evidence_1/evidence/reorder",
        json={"exhibit_ids": [exhibit_id]},
        headers=ADMIN_HEADERS,
    )
    assert res.status_code == 200

    # 9. Office generates evidence packet
    res = client.post(
        "/api/office/jobs/job_api_evidence_1/generate-evidence-packet",
        json={"version": 1, "included_only": False},
        headers=ADMIN_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["version"] == 1

    # 10. Office downloads evidence packet
    res = client.get("/api/office/jobs/job_api_evidence_1/download-evidence-packet?version=1", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"

    # 11. Office downloads nonexistent job packet (404)
    res = client.get("/api/office/jobs/job_nonexistent_xyz/download-evidence-packet?version=1", headers=ADMIN_HEADERS)
    assert res.status_code == 404


def test_field_and_office_actions_api_endpoints(temp_db, monkeypatch):
    """Test GET /api/field/actions/today and GET /api/office/actions/triage."""
    monkeypatch.setattr("app.core.database.get_db_path", lambda: Path(temp_db))
    _seed_job(temp_db, "job_act_1", status="LEAD_CAPTURED", rep_id="rep_api_1")

    client = TestClient(app)
    rep1_token = create_access_token(role="field", rep_id="rep_api_1", rep_name="Rep One")
    admin_token = create_access_token(role="admin")

    res = client.get("/api/field/actions/today", headers={"x-internal-token": rep1_token})
    assert res.status_code == 200
    data = res.json()
    assert "actions" in data
    assert "count" in data

    res = client.get("/api/office/actions/triage", headers={"x-internal-token": admin_token})
    assert res.status_code == 200
    triage = res.json()
    assert "triage" in triage
    assert "stalled_jobs" in triage["triage"]

