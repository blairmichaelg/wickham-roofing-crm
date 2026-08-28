"""
Unit Tests for Xactimate Line-Item Coverage Expansion (Sprint 3).

Tests:
1. RFG STEEP: Steep roof safety charges on pitch >= 7/12.
2. RFG RIDGC+: High-profile architectural ridge cap upgrades.
3. SFG GUTA: Gutter replacement triggered exclusively by documented storm damage.
4. DMO DUMP: Debris haul-off container calculation on tear-off jobs.
5. RFG RENAIL: IRC R905.2.1 roof decking re-nailing.
6. Pipeline integration for supplemental flag evaluation.
"""

import uuid
import pytest

from app.core.supplement_models import EagleViewData
from app.core.database import get_connection, seed_supplement_rules
from app.core.pipeline import generate_and_gate_flags
from app.services.supplement_engine import SupplementEngine


def test_rfg_steep_evaluation():
    # >= 7/12 triggers
    is_steep, pitch = SupplementEngine.evaluate_steep_charge("7/12")
    assert is_steep is True
    assert pitch == 7.0

    is_steep, pitch = SupplementEngine.evaluate_steep_charge("9/12")
    assert is_steep is True
    assert pitch == 9.0

    is_steep, pitch = SupplementEngine.evaluate_steep_charge(8.5)
    assert is_steep is True
    assert pitch == 8.5

    # < 7/12 does NOT trigger
    is_steep, pitch = SupplementEngine.evaluate_steep_charge("4/12")
    assert is_steep is False
    assert pitch == 4.0

    is_steep, pitch = SupplementEngine.evaluate_steep_charge("6/12")
    assert is_steep is False
    assert pitch == 6.0

    is_steep, pitch = SupplementEngine.evaluate_steep_charge(5.0)
    assert is_steep is False
    assert pitch == 5.0


def test_rfg_ridge_cap_upgrade_evaluation():
    # Architectural / Dimensional triggers RIDGC+
    is_up, code = SupplementEngine.evaluate_ridge_cap_upgrade("Architectural")
    assert is_up is True
    assert code == "RFG RIDGC+"

    is_up, code = SupplementEngine.evaluate_ridge_cap_upgrade("GAF Timberline HDZ Dimensional")
    assert is_up is True
    assert code == "RFG RIDGC+"

    is_up, code = SupplementEngine.evaluate_ridge_cap_upgrade("CertainTeed Landmark")
    assert is_up is True
    assert code == "RFG RIDGC+"

    # 3-Tab / Standard does NOT upgrade
    is_up, code = SupplementEngine.evaluate_ridge_cap_upgrade("3-Tab 25 Yr Composition")
    assert is_up is False
    assert code == "RFG RIDGC"

    is_up, code = SupplementEngine.evaluate_ridge_cap_upgrade(None)
    assert is_up is False
    assert code == "RFG RIDGC"


def test_sfg_guta_gutter_replacement_evaluation():
    # Triggered when gutter damage is documented
    assert SupplementEngine.evaluate_gutter_replacement(["gutter_damage", "hail_bruise"]) is True
    assert SupplementEngine.evaluate_gutter_replacement("Extensive gutter impact and denting along east eave") is True
    assert SupplementEngine.evaluate_gutter_replacement(gutters_damaged=True) is True
    assert SupplementEngine.evaluate_gutter_replacement([{"finding": "dented downspout", "severity": "high"}]) is True

    # NOT triggered when no gutter damage documented
    assert SupplementEngine.evaluate_gutter_replacement(["shingle_crease", "missing_ridge_cap"]) is False
    assert SupplementEngine.evaluate_gutter_replacement("Hail damage to slope 1 and slope 2 field shingles") is False
    assert SupplementEngine.evaluate_gutter_replacement([]) is False
    assert SupplementEngine.evaluate_gutter_replacement(None) is False


def test_dmo_dump_dumpster_hauloff_evaluation():
    # Tear-off triggers DMO DUMP with proper container scaling
    is_req, count = SupplementEngine.evaluate_dumpster_hauloff(tear_off_required=True, squares=25.0)
    assert is_req is True
    assert count == 1

    is_req, count = SupplementEngine.evaluate_dumpster_hauloff(tear_off_required=True, squares=45.0)
    assert is_req is True
    assert count == 2

    is_req, count = SupplementEngine.evaluate_dumpster_hauloff(tear_off_required=True, squares=85.0)
    assert is_req is True
    assert count == 3

    # Non-tear-off does NOT trigger
    is_req, count = SupplementEngine.evaluate_dumpster_hauloff(tear_off_required=False, squares=50.0)
    assert is_req is False
    assert count == 0


def test_rfg_renail_decking_evaluation():
    assert SupplementEngine.evaluate_decking_renail(tear_off_required=True) is True
    assert SupplementEngine.evaluate_decking_renail(tear_off_required=False) is False


def test_supplement_flags_pipeline_integration():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, shingle_type, damage_signals) 
            VALUES (?, 'Steve Builder', '100 Steep Way', 'Thomasville', 'GA', '31792', '229-555-0199', 'LEAD_CAPTURED', 'Architectural Shingles', '["gutter_damage", "shingle_hail"]')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    seed_supplement_rules()

    ev_data = EagleViewData(
        total_area_sf=3200.0,
        predominant_pitch="8/12",
        ridge_lf=40.0,
        hip_lf=20.0,
        valley_lf=30.0,
        eaves_lf=120.0,
        rake_lf=60.0,
        drip_edge_lf=180.0,
        total_facets=4,
        flashing_lf=15.0,
        step_flashing_lf=10.0,
    )

    # In Georgia (ice_barrier_required=False)
    manual_review = generate_and_gate_flags(job_id, ice_barrier_required=False, ev_data=ev_data)
    assert manual_review is False

    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT r.required_child_code, f.quantity_delta, f.notes 
            FROM supplement_flags f 
            JOIN supplement_rules r ON f.rule_id = r.id 
            WHERE f.job_id = ? AND f.triggered = 1""",
            (job_id,)
        )
        flags = {row["required_child_code"]: row for row in cursor.fetchall()}
    finally:
        conn.close()

    # Verify all expected codes are generated
    assert "RFG STEEP" in flags
    assert flags["RFG STEEP"]["quantity_delta"] == 32.0

    assert "RFG RIDGC+" in flags
    assert flags["RFG RIDGC+"]["quantity_delta"] == 60.0  # 40 ridge + 20 hip

    assert "SFG GUTA" in flags
    assert flags["SFG GUTA"]["quantity_delta"] == 120.0  # eaves length

    assert "DMO DUMP" in flags
    assert flags["DMO DUMP"]["quantity_delta"] == 2.0  # ceil(32 / 30) = 2

    assert "RFG RENAIL" in flags
    assert flags["RFG RENAIL"]["quantity_delta"] == 32.0

    # Climate gate verified: RFG IWS must NOT be present when ice_barrier_required=False
    assert "RFG IWS" not in flags
