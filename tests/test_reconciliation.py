"""
Unit tests for the deterministic reconciliation engine.
"""

import pytest

from app.core.reconciliation import reconcile
from app.core.supplement_models import EagleViewData, LineItem, StatementOfLoss


def test_reconcile_square_variance():
    ev = EagleViewData(
        total_area_sf=6788.0, rake_lf=0, valley_lf=0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=1, predominant_pitch="10/12"
    )
    # Default waste factor = 0.15
    # Normalized SQ: (6788 / 100) * 1.15 = 78.062 -> rounded by pure math?
    # Wait, the code now does: ev_normalized_squares = (ev.total_area_sf / 100.0) * (1.0 + waste_factor)
    # 6788 / 100 = 67.88. 67.88 * 1.15 = 78.062
    
    sol = StatementOfLoss(
        line_items=[
            LineItem(trade="Roof", code="1", description="Remove", quantity=23.0, unit_of_measure="SQ"),
            LineItem(trade="Roof", code="2", description="Replace", quantity=25.0, unit_of_measure="SQ"),
        ],
        overhead_and_profit_included=True
    )
    
    # sol_total_rfg_squares should be max(23.0, 25.0) = 25.0
    report = reconcile(ev, sol, "job_1")
    
    assert report.sol_total_rfg_squares == 25.0
    assert report.square_variance == pytest.approx(53.06, rel=1e-3)
    assert report.ev_normalized_squares == pytest.approx(78.062, rel=1e-3)
    
    area_disc = next((d for d in report.discrepancies if d.category == "Area Shortage"), None)
    assert area_disc is not None
    assert area_disc.variance == pytest.approx(53.06, rel=1e-3)


def test_reconcile_missing_ice_and_water():
    ev = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=50.0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=1, predominant_pitch="10/12"
    )
    
    sol = StatementOfLoss(
        line_items=[
            LineItem(trade="Roof", code="1", description="Shingles", quantity=10.0, unit_of_measure="SQ"),
        ],
        overhead_and_profit_included=True
    )
    
    report = reconcile(ev, sol, "job_1")
    
    iw_disc = next((d for d in report.discrepancies if d.category == "Missing Ice & Water Shield"), None)
    assert iw_disc is not None
    assert iw_disc.ev_value == 50.0


def test_reconcile_found_ice_and_water():
    ev = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=50.0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=1, predominant_pitch="10/12"
    )
    
    sol = StatementOfLoss(
        line_items=[
            LineItem(trade="Roof", code="1", description="Ice and Water Barrier", quantity=200.0, unit_of_measure="SF"),
        ],
        overhead_and_profit_included=True
    )
    
    report = reconcile(ev, sol, "job_1")
    
    iw_disc = next((d for d in report.discrepancies if d.category == "Missing Ice & Water Shield"), None)
    assert iw_disc is None


def test_reconcile_ridge_hip_shortage():
    ev = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=0, ridge_lf=50.0, hip_lf=50.0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=1, predominant_pitch="10/12"
    )
    # Total ridge/hip = 100.0
    
    sol = StatementOfLoss(
        line_items=[
            LineItem(trade="Roof", code="1", description="Ridge Cap", quantity=40.0, unit_of_measure="LF"),
        ],
        overhead_and_profit_included=True
    )
    
    report = reconcile(ev, sol, "job_1")
    
    ridge_disc = next((d for d in report.discrepancies if d.category == "Ridge/Hip Cap Shortage"), None)
    assert ridge_disc is not None
    assert ridge_disc.variance == 60.0


def test_reconcile_missing_op():
    ev = EagleViewData(
        total_area_sf=1000.0, rake_lf=0, valley_lf=0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=1, predominant_pitch="10/12"
    )
    
    sol = StatementOfLoss(
        line_items=[
            LineItem(
                trade="Roofing",
                code="RFG 300S",
                description="Test item"
            )
        ],
        overhead_and_profit_included=False
    )
    
    report = reconcile(ev, sol, "job_1")
    
    op_disc = next((d for d in report.discrepancies if d.category == "Missing O&P"), None)
    assert op_disc is not None


def test_reconcile_bom_calculation():
    ev = EagleViewData(
        total_area_sf=6788.0, rake_lf=114.0, valley_lf=288.0, ridge_lf=120.0, hip_lf=315.0,
        eaves_lf=276.0, drip_edge_lf=390.0, flashing_lf=3.0, step_flashing_lf=16.0,
        total_facets=26, predominant_pitch="10/12"
    )
    sol = StatementOfLoss(line_items=[], overhead_and_profit_included=True)
    report = reconcile(ev, sol, "job_1")
    
    # Normalized SQ: 67.88 * 1.15 = 78.062
    bom = report.material_bom
    assert bom.field_shingle_bundles == 235  # ceil(78.062 * 3) = 235
    assert bom.starter_bundles == 4          # ceil((276 + 114) / 100) -> 390 / 100 = 3.9 -> 4
    assert bom.ridge_cap_bundles == 4        # ceil(120 / 33) -> 120 / 33 = 3.63 -> 4
    assert bom.ice_water_rolls == 5          # ceil((288 * 3) / 200) -> 864 / 200 = 4.32 -> 5
    assert bom.underlayment_rolls == 8       # ceil(78.062 / 10) -> 7.8062 -> 8
    assert bom.drip_edge_pieces == 39        # ceil(390 / 10) = 39


def test_weaponized_waste_explanation():
    ev = EagleViewData(
        total_area_sf=6788.0, rake_lf=0, valley_lf=50.0, ridge_lf=0, hip_lf=0,
        eaves_lf=0, drip_edge_lf=0, flashing_lf=0, step_flashing_lf=0,
        total_facets=4, predominant_pitch="10/12"
    )
    sol = StatementOfLoss(line_items=[], overhead_and_profit_included=True)
    # Using specific waste factor to verify the string interpolation
    report = reconcile(ev, sol, "job_1", waste_factor=0.14)
    
    # 4 facets * 0.2 = 0.8
    # 10/12 pitch = (10 - 7) * 0.5 = 1.5
    # 50 LF valley / 50 * 0.5 = 0.5
    # Total Score = 2.8
    assert report.waste_explanation == (
        "A 14% waste factor is mathematically required (Complexity Score: 2.80) "
        "due to 4 intersecting facets, 50.0 LF of valleys, and a 10/12 pitch."
    )


