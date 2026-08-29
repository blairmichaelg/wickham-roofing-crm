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

    # Shingle waste adjustment for complex geometry (30 LF valley + 20 LF hip)
    assert "RFG 300S" in flags
    assert flags["RFG 300S"]["quantity_delta"] == 1.6  # 32 SQ * (15% - 10%) = 1.6 SQ

    # Climate gate verified: RFG IWS must NOT be present when ice_barrier_required=False
    assert "RFG IWS" not in flags


def test_shingle_waste_pipeline_simple_geometry_no_flag():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, shingle_type, damage_signals) 
            VALUES (?, 'Simple Gable', '200 Flat Way', 'Thomasville', 'GA', '31792', '229-555-0200', 'LEAD_CAPTURED', '3-Tab Shingles', '[]')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    seed_supplement_rules()

    # Simple gable roof: 0 valleys, 0 hips
    ev_data = EagleViewData(
        total_area_sf=2000.0,
        predominant_pitch="4/12",
        ridge_lf=40.0,
        hip_lf=0.0,
        valley_lf=0.0,
        eaves_lf=80.0,
        rake_lf=40.0,
        drip_edge_lf=120.0,
        total_facets=2,
        flashing_lf=0.0,
        step_flashing_lf=0.0,
    )

    generate_and_gate_flags(job_id, ice_barrier_required=False, ev_data=ev_data, carrier_waste_pct=10.0)

    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT r.required_child_code 
            FROM supplement_flags f 
            JOIN supplement_rules r ON f.rule_id = r.id 
            WHERE f.job_id = ? AND f.triggered = 1""",
            (job_id,)
        )
        codes = [row["required_child_code"] for row in cursor.fetchall()]
    finally:
        conn.close()

    assert "RFG 300S" not in codes
    assert "RFG STEEP" not in codes


def test_shingle_waste_pipeline_high_carrier_waste_no_flag():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, shingle_type, damage_signals) 
            VALUES (?, 'Generous Carrier', '300 High Waste Way', 'Thomasville', 'GA', '31792', '229-555-0300', 'LEAD_CAPTURED', 'Architectural', '[]')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    seed_supplement_rules()

    ev_data = EagleViewData(
        total_area_sf=3000.0,
        predominant_pitch="6/12",
        ridge_lf=30.0,
        hip_lf=25.0,
        valley_lf=40.0,
        eaves_lf=100.0,
        rake_lf=50.0,
        drip_edge_lf=150.0,
        total_facets=6,
        flashing_lf=0.0,
        step_flashing_lf=0.0,
    )

    # Carrier already gave 15% or higher
    generate_and_gate_flags(job_id, ice_barrier_required=False, ev_data=ev_data, carrier_waste_pct=15.0)

    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT r.required_child_code 
            FROM supplement_flags f 
            JOIN supplement_rules r ON f.rule_id = r.id 
            WHERE f.job_id = ? AND f.triggered = 1""",
            (job_id,)
        )
        codes = [row["required_child_code"] for row in cursor.fetchall()]
    finally:
        conn.close()

    assert "RFG 300S" not in codes


def test_supplemental_xactimate_pricing_resolution():
    from app.core.database import get_pricing_ledger, seed_default_pricing
    seed_default_pricing()
    pricing = get_pricing_ledger()

    # Confirm all new supplemental codes resolve to non-zero baseline rates
    assert pricing.get("rfg_steep_per_sq", 0.0) == 35.0
    assert pricing.get("rfg_ridgc_plus_per_lf", 0.0) == 8.50
    assert pricing.get("sfg_guta_per_lf", 0.0) == 12.0
    assert pricing.get("dmo_dump_per_container", 0.0) == 450.0
    assert pricing.get("rfg_renail_per_sq", 0.0) == 15.0
    assert pricing.get("rfg_waste_adjustment_per_sq", 0.0) == 105.0

    # Calculate priced line amounts for a sample job with all 5 flags + waste adjustment
    quantities = {
        "rfg_steep_per_sq": 32.0,
        "rfg_ridgc_plus_per_lf": 60.0,
        "sfg_guta_per_lf": 120.0,
        "dmo_dump_per_container": 2.0,
        "rfg_renail_per_sq": 32.0,
        "rfg_waste_adjustment_per_sq": 1.6,
    }

    line_amounts = {k: qty * pricing[k] for k, qty in quantities.items()}
    assert line_amounts["rfg_steep_per_sq"] == 1120.0  # 32 * 35
    assert line_amounts["rfg_ridgc_plus_per_lf"] == 510.0  # 60 * 8.50
    assert line_amounts["sfg_guta_per_lf"] == 1440.0  # 120 * 12
    assert line_amounts["dmo_dump_per_container"] == 900.0  # 2 * 450
    assert line_amounts["rfg_renail_per_sq"] == 480.0  # 32 * 15
    assert line_amounts["rfg_waste_adjustment_per_sq"] == 168.0  # 1.6 * 105
    assert all(amt > 0 for amt in line_amounts.values())


@pytest.mark.asyncio
async def test_supplement_pdf_pricing_resolution_end_to_end():
    import os
    from pathlib import Path
    import pdfplumber
    from app.services.pdf.supplement import SupplementGenerator
    from app.core.supplement_models import DiscrepancyReport, MaterialBOM
    from app.core.database import seed_default_pricing

    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, shingle_type, damage_signals) 
            VALUES (?, 'Pricing Verify', '500 Valuation Lane', 'Thomasville', 'GA', '31792', '229-555-0500', 'LEAD_CAPTURED', 'GAF Timberline HDZ Architectural', '["gutter_damage"]')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    seed_supplement_rules()
    seed_default_pricing()

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

    # 1. Generate supplement flags through real pipeline function
    manual_review = generate_and_gate_flags(
        job_id=job_id, 
        ice_barrier_required=False, 
        ev_data=ev_data,
        carrier_waste_pct=10.0
    )
    assert manual_review is False

    # 2. Build db_context as pipeline does
    conn = get_connection()
    try:
        cursor = conn.execute('''
            SELECT r.citation_text, r.citation_type, r.required_child_code, r.climate_dependent, f.quantity_delta, f.notes
            FROM supplement_flags f
            JOIN supplement_rules r ON f.rule_id = r.id
            WHERE f.job_id = ? AND f.triggered = 1
        ''', (job_id,))
        rules = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    db_context = {
        "ice_barrier_required": False,
        "jurisdiction_code_version": "2021_IRC",
        "rules": rules,
        "weather": None
    }

    report = DiscrepancyReport(
        job_id=job_id,
        ev_normalized_squares=36.8,
        sol_total_rfg_squares=32.0,
        square_variance=4.8,
        waste_explanation="15% waste mathematically required",
        material_bom=MaterialBOM(
            field_shingle_bundles=96,
            starter_bundles=6,
            ridge_cap_bundles=4,
            ice_water_rolls=0,
            underlayment_rolls=4,
            drip_edge_pieces=18
        ),
        discrepancies=[]
    )

    job_dict = {
        "id": job_id,
        "homeowner_name": "Pricing Verify",
        "address_line1": "500 Valuation Lane",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31792",
        "claim_number": "CLM-VAL-101",
        "insurance_company": "State Farm",
    }

    # 3. Generate real supplement PDF
    pdf_gen = SupplementGenerator()
    pdf_path = await pdf_gen.generate_supplement_pdf(report, "Technical narrative justification.", job=job_dict, db_context=db_context)
    
    try:
        assert os.path.exists(pdf_path)
        
        # 4. Extract text and verify non-zero priced dollar amounts for all 6 line items
        with pdfplumber.open(pdf_path) as pdf:
            full_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            
            # Assert all 6 codes appear with their calculated dollar totals
            assert "RFG STEEP" in full_text
            assert "$1,120.00" in full_text  # 32.00 SQ * $35.00

            assert "RFG RIDGC+" in full_text
            assert "$510.00" in full_text    # 60.00 LF * $8.50

            assert "SFG GUTA" in full_text
            assert "$1,440.00" in full_text  # 120.00 LF * $12.00

            assert "DMO DUMP" in full_text
            assert "$900.00" in full_text    # 2.00 EA * $450.00

            assert "RFG RENAIL" in full_text
            assert "$480.00" in full_text    # 32.00 SQ * $15.00

            assert "RFG 300S" in full_text
            assert "$168.00" in full_text    # 1.60 SQ * $105.00

            assert "Total Supplemental Valuation" in full_text
    finally:
        # 5. Clean up temporary test PDF
        if os.path.exists(pdf_path):
            os.remove(pdf_path)


def test_rebuttal_and_frontend_rules_consumption():
    # Test citation extraction logic with self-referential / child-equal rule
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, shingle_type, damage_signals) 
            VALUES (?, 'Citation Test', '600 Rule St', 'Thomasville', 'GA', '31792', '229-555-0600', 'LEAD_CAPTURED', 'Architectural', '[]')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    seed_supplement_rules()

    ev_data = EagleViewData(
        total_area_sf=3000.0,
        predominant_pitch="5/12",
        ridge_lf=30.0,
        hip_lf=20.0,
        valley_lf=25.0,
        eaves_lf=100.0,
        rake_lf=40.0,
        drip_edge_lf=140.0,
        total_facets=4,
        flashing_lf=0.0,
        step_flashing_lf=0.0,
    )

    generate_and_gate_flags(job_id, ice_barrier_required=False, ev_data=ev_data, carrier_waste_pct=10.0)

    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT r.required_child_code, r.citation_text, r.citation_type, f.quantity_delta, f.notes
               FROM supplement_flags f
               JOIN supplement_rules r ON f.rule_id = r.id
               WHERE f.job_id = ? AND f.triggered = 1""",
            (job_id,)
        ).fetchall()
        citations = [dict(r) for r in rows]
    finally:
        conn.close()

    # Confirm RFG 300S is present and correctly processed without collision or drop
    codes = [c["required_child_code"] for c in citations]
    assert "RFG 300S" in codes
    waste_citation = next(c for c in citations if c["required_child_code"] == "RFG 300S")
    assert waste_citation["quantity_delta"] == 1.5  # 30 SQ * 0.05
    assert "Complex Geometry" in waste_citation["citation_text"]


