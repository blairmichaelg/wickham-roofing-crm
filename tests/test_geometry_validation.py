import pytest

from app.core.geometry_validation import (
    PITCH_MULTIPLIERS,
    get_pitch_multiplier,
    requires_steep_charge,
    validate_area_to_footprint,
    validate_edge_completeness,
    validate_pitch,
)


def test_pitch_multipliers_table():
    assert len(PITCH_MULTIPLIERS) == 24
    assert PITCH_MULTIPLIERS[12] == round((144 + 144) ** 0.5 / 12.0, 4)  # ~1.4142
    assert PITCH_MULTIPLIERS[4] == round((16 + 144) ** 0.5 / 12.0, 4)   # ~1.0541


def test_validate_pitch_valid_strings():
    assert validate_pitch("6/12") == 6.0
    assert validate_pitch("8.5/12") == 8.5
    assert validate_pitch("12/12") == 12.0
    assert validate_pitch("7") == 7.0
    assert validate_pitch(4) == 4.0
    assert validate_pitch(9.25) == 9.25


def test_validate_pitch_invalid_and_out_of_range():
    with pytest.raises(ValueError, match="out of standard physical range"):
        validate_pitch("0/12")
    with pytest.raises(ValueError, match="out of standard physical range"):
        validate_pitch("25/12")
    with pytest.raises(ValueError, match="Malformed pitch format"):
        validate_pitch("6/12/18")
    with pytest.raises(ValueError, match="Standard roof pitch run must be 12"):
        validate_pitch("6/10")
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_pitch("   ")
    with pytest.raises(ValueError, match="Pitch must not be None"):
        validate_pitch(None)


def test_validate_area_to_footprint_valid():
    # 2000 SF roof on 6/12 pitch, perimeter 200 LF -> max area = (200/4)^2 = 2500 SF. Footprint ~ 1788 SF < 2500 SF.
    multiplier = get_pitch_multiplier(6.0)
    validate_area_to_footprint(2000.0, multiplier, 200.0)


def test_validate_area_to_footprint_impossible():
    # 10,000 SF roof with only 40 LF perimeter -> max area is (40/4)^2 = 100 SF. Impossible!
    multiplier = get_pitch_multiplier(4.0)
    with pytest.raises(ValueError, match="Impossible geometry"):
        validate_area_to_footprint(10000.0, multiplier, 40.0)


def test_validate_area_to_footprint_zero_perimeter_nonzero_area():
    with pytest.raises(ValueError, match="Non-zero roof area requires a non-zero perimeter"):
        validate_area_to_footprint(1500.0, 1.1, 0.0)


def test_validate_edge_completeness():
    # Valid roof with edges
    validate_edge_completeness(2500.0, eaves_lf=120.0, rake_lf=60.0, ridge_lf=40.0)

    # Invalid: 2500 SF but all 0 edges
    with pytest.raises(ValueError, match="Incomplete edge data"):
        validate_edge_completeness(2500.0, eaves_lf=0.0, rake_lf=0.0, ridge_lf=0.0)


def test_requires_steep_charge():
    assert not requires_steep_charge(6.5)
    assert not requires_steep_charge(6.99)
    assert requires_steep_charge(7.0)
    assert requires_steep_charge(8.0)
    assert requires_steep_charge(12.0)
