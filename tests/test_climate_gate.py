import pytest

from app.core.database import get_connection
from app.core.pipeline import generate_and_gate_flags


@pytest.fixture
def setup_test_jobs():
    conn = get_connection()
    try:
        # Create a Georgia job (climate gate False)
        conn.execute('''
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("TEST-GA-JOB", "GA Homeowner", "123 GA St", "Atlanta", "GA", "30000", "555-5555"))
        
        # Create a Minnesota job (climate gate True)
        conn.execute('''
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("TEST-MN-JOB", "MN Homeowner", "456 MN St", "Minneapolis", "MN", "55000", "555-5555"))
        
        # Create a Virginia job (climate gate None)
        conn.execute('''
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("TEST-VA-JOB", "VA Homeowner", "789 VA St", "Richmond", "VA", "23218", "555-5555"))
        
        conn.commit()
    finally:
        conn.close()
        
    yield
    
    conn = get_connection()
    try:
        conn.execute("DELETE FROM supplement_flags WHERE job_id IN ('TEST-GA-JOB', 'TEST-MN-JOB', 'TEST-VA-JOB')")
        conn.execute("DELETE FROM jobs WHERE id IN ('TEST-GA-JOB', 'TEST-MN-JOB', 'TEST-VA-JOB')")
        conn.commit()
    finally:
        conn.close()

def test_climate_gate_blocks_iws_in_georgia(setup_test_jobs):
    """
    Asserts that supplement_flags contains zero rows for a climate-dependent rule (IWS)
    on a Georgia job where ice_barrier_required is False.
    """
    from app.core.supplement_models import EagleViewData
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=0.0,
        step_flashing_lf=0.0, total_facets=2, predominant_pitch="6/12"
    )
    # Trigger flag generation (ice_barrier_required = False)
    generate_and_gate_flags("TEST-GA-JOB", ice_barrier_required=False, ev_data=ev_data)
    
    conn = get_connection()
    try:
        # Check IWS rule flags (climate_dependent = 1)
        cursor = conn.execute('''
            SELECT f.id FROM supplement_flags f
            JOIN supplement_rules r ON f.rule_id = r.id
            WHERE f.job_id = ? AND r.climate_dependent = 1
        ''', ("TEST-GA-JOB",))
        iws_flags = cursor.fetchall()
        assert len(iws_flags) == 0, "Georgia job incorrectly generated a climate-dependent flag (IWS)"
        
        # Ensure DRIP rule (climate_dependent = 0) STILL generated
        cursor = conn.execute('''
            SELECT f.id FROM supplement_flags f
            JOIN supplement_rules r ON f.rule_id = r.id
            WHERE f.job_id = ? AND r.climate_dependent = 0
        ''', ("TEST-GA-JOB",))
        drip_flags = cursor.fetchall()
        assert len(drip_flags) > 0, "Georgia job incorrectly blocked non-climate dependent rules (DRIP)"
    finally:
        conn.close()

def test_climate_gate_allows_iws_in_minnesota(setup_test_jobs):
    """
    Asserts that supplement_flags DOES contain a row for the climate-dependent rule (IWS)
    on a Minnesota job where ice_barrier_required is True.
    """
    from app.core.supplement_models import EagleViewData
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=0.0,
        step_flashing_lf=0.0, total_facets=2, predominant_pitch="6/12"
    )
    # Trigger flag generation (ice_barrier_required = True)
    generate_and_gate_flags("TEST-MN-JOB", ice_barrier_required=True, ev_data=ev_data)
    
    conn = get_connection()
    try:
        # Check IWS rule flags (climate_dependent = 1)
        cursor = conn.execute('''
            SELECT f.id FROM supplement_flags f
            JOIN supplement_rules r ON f.rule_id = r.id
            WHERE f.job_id = ? AND r.climate_dependent = 1
        ''', ("TEST-MN-JOB",))
        iws_flags = cursor.fetchall()
        assert len(iws_flags) > 0, "Minnesota job incorrectly blocked a climate-dependent flag (IWS)"
    finally:
        conn.close()

def test_climate_gate_blocks_iws_when_ambiguous(setup_test_jobs):
    """
    Asserts that supplement_flags contains zero rows for a climate-dependent rule (IWS)
    on an ambiguous job (e.g. Virginia) where ice_barrier_required is None.
    """
    from app.core.supplement_models import EagleViewData
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=0.0,
        step_flashing_lf=0.0, total_facets=2, predominant_pitch="6/12"
    )
    # Trigger flag generation (ice_barrier_required = None)
    generate_and_gate_flags("TEST-VA-JOB", ice_barrier_required=None, ev_data=ev_data)
    
    conn = get_connection()
    try:
        # Check IWS rule flags (climate_dependent = 1)
        cursor = conn.execute('''
            SELECT f.id FROM supplement_flags f
            JOIN supplement_rules r ON f.rule_id = r.id
            WHERE f.job_id = ? AND r.climate_dependent = 1
        ''', ("TEST-VA-JOB",))
        iws_flags = cursor.fetchall()
        assert len(iws_flags) == 0, "Ambiguous job incorrectly generated a climate-dependent flag (IWS)"
    finally:
        conn.close()

def test_generate_and_gate_flags_multi_failure_scoping(setup_test_jobs):
    """
    Proves that if multiple rules trigger ValueError, the loop catches them independently,
    flags them for manual review, and successfully batch-inserts everything.
    """
    import uuid

    from app.core.supplement_models import EagleViewData
    # Negative pitch will cause ValueError in IWS calculation
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=0.0,
        step_flashing_lf=0.0, total_facets=2, predominant_pitch="-6/12"
    )
    
    conn = get_connection()
    try:
        # Insert a duplicate RFG IWS rule to simulate two separate math failures
        conn.execute('''
            INSERT INTO supplement_rules (id, parent_code, required_child_code, citation_text, citation_type, trigger_logic_name, climate_dependent)
            VALUES (?, 'RFG', 'RFG IWS', 'Fake Rule 2', 'IRC', 'calculate_ice_and_water_rolls', 1)
        ''', (str(uuid.uuid4()),))
        conn.commit()
        
        # Trigger generation. Minnesota job has ice_barrier_required=True
        # This should process two RFG IWS rules, both should ValueError.
        manual_review = generate_and_gate_flags("TEST-MN-JOB", ice_barrier_required=True, ev_data=ev_data)
        
        assert manual_review is True
        
        cursor = conn.execute("SELECT * FROM supplement_flags WHERE job_id = ?", ("TEST-MN-JOB",))
        flags = cursor.fetchall()
        
        # We should have multiple flags inserted (DRIP, plus two IWS flags)
        assert len(flags) >= 2
        
        iws_flags = [f for f in flags if "MANUAL REVIEW REQUIRED" in str(f["notes"])]
        assert len(iws_flags) == 2, "Loop did not correctly catch and insert multiple independent failures."
        
    finally:
        conn.close()

def test_clean_slate_idempotency_for_flags(setup_test_jobs):
    """
    Proves that generate_and_gate_flags deletes old flags for the job and
    only leaves the newly generated flags, without throwing a UNIQUE constraint error.
    """
    from app.core.supplement_models import EagleViewData
    ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=0.0,
        step_flashing_lf=0.0, total_facets=2, predominant_pitch="6/12"
    )
    
    # Run 1: generate flags for MN job
    generate_and_gate_flags("TEST-MN-JOB", ice_barrier_required=True, ev_data=ev_data)
    
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM supplement_flags WHERE job_id = ?", ("TEST-MN-JOB",))
        count_run_1 = cursor.fetchone()[0]
        assert count_run_1 > 0
    finally:
        conn.close()
        
    # Run 2: generate flags again. Should not throw constraint error, and count should be the same
    generate_and_gate_flags("TEST-MN-JOB", ice_barrier_required=True, ev_data=ev_data)
    
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM supplement_flags WHERE job_id = ?", ("TEST-MN-JOB",))
        count_run_2 = cursor.fetchone()[0]
        assert count_run_2 == count_run_1
    finally:
        conn.close()

