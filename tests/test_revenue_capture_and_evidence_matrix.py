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

from app.core.database import run_migrations, get_db_connection
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
            INSERT INTO jobs (id, homeowner_name, phone, address_line1, city, state, postal_code, status, canvasser_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (job_id, f"Homeowner {job_id[:4]}", "229-555-0199", "123 Oak St", "Thomasville", "GA", postal_code, status, rep_id),
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
