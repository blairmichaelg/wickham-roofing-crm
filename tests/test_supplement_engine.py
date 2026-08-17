import pytest

from app.services.supplement_engine import SupplementEngine


def test_iws_standard_calculation():
    """Happy path: 6/12 pitch, standard lengths."""
    rolls = SupplementEngine.calculate_ice_and_water_rolls(
        pitch=6.0,
        eave_length_ft=50.0,
        valley_length_ft=20.0
    )
    # total_horizontal_in = 12 + 6 + 24 = 42"
    # rise_in = (42 / 12) * 6 = 21"
    # hypot = sqrt(42^2 + 21^2) = 46.957"
    # sloped_ft = 46.957 / 12 = 3.913 ft
    # eave_sqft = 50 * 3.913 = 195.65
    # valley_sqft = 20 * 3 = 60
    # total = 255.65 sqft
    # rolls = ceil(255.65 / 66.7) = ceil(3.83) = 4
    assert rolls == 4

def test_iws_zero_pitch():
    """Edge case: flat roof (pitch=0). Should use pure horizontal distance."""
    rolls = SupplementEngine.calculate_ice_and_water_rolls(
        pitch=0.0,
        eave_length_ft=50.0,
        valley_length_ft=0.0
    )
    # total_horizontal = 42". rise = 0. hypot = 42". sloped_ft = 3.5 ft.
    # eave_sqft = 50 * 3.5 = 175.
    # rolls = ceil(175 / 66.7) = 3
    assert rolls == 3

def test_iws_negative_lengths_clamped():
    """Edge case: negative lengths or pitch should raise ValueError."""
    with pytest.raises(ValueError, match="Malformed EagleView inputs"):
        SupplementEngine.calculate_ice_and_water_rolls(
            pitch=-4.0,
            eave_length_ft=-100.0,
            valley_length_ft=-50.0
        )

def test_iws_missing_or_zero_lengths():
    """Edge case: completely missing lengths (e.g. gable only, no eaves/valleys)."""
    rolls = SupplementEngine.calculate_ice_and_water_rolls(
        pitch=8.0,
        eave_length_ft=0.0,
        valley_length_ft=0.0
    )
    assert rolls == 0
