from app.core.complexity import build_waste_explanation
from app.core.supplement_models import EagleViewData


def test_build_waste_explanation_weaponized():
    """
    Test that build_waste_explanation properly interpolates all fields
    and strictly enforces the weaponized phrasing. Reverting the string
    should cause this test to fail.
    """
    ev_data = EagleViewData(
        total_area_sf=1000.0,
        rake_lf=0,
        valley_lf=50.0,
        ridge_lf=0,
        hip_lf=0,
        eaves_lf=0,
        drip_edge_lf=0,
        flashing_lf=0,
        step_flashing_lf=0,
        total_facets=4,
        predominant_pitch="10/12"
    )
    
    score = 2.80
    waste_pct = 0.14
    
    result = build_waste_explanation(ev_data, score, waste_pct)
    
    expected = (
        "A 14% waste factor is mathematically required (Complexity Score: 2.80) "
        "due to 4 intersecting facets, 50.0 LF of valleys, and a 10/12 pitch."
    )
    
    assert result == expected
    assert "14%" in result
    assert "2.80" in result
    assert "4 intersecting facets" in result
    assert "50.0 LF of valleys" in result
    assert "10/12 pitch" in result
