"""
tests/test_property_supplement.py — Property-based tests for SupplementEngine math.

Uses Hypothesis to stress-test the mathematical kernel far beyond hand-written
examples, covering the full domain of valid inputs.
"""

import math

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.services.supplement_engine import ROLL_SQFT_YIELD, SupplementEngine

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

# Valid roof pitches: 0 (flat) to 24/12 (steep)
pitch_st = st.floats(min_value=0.0, max_value=24.0, allow_nan=False, allow_infinity=False)

# Linear footage values: 0 to 2000 ft (large commercial roof)
lf_st = st.floats(min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False)

# Non-negative integers for hips count
hips_st = st.integers(min_value=0, max_value=20)

# Carrier waste percentage
waste_pct_st = st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# IWS roll calculation properties
# ---------------------------------------------------------------------------


class TestIWSProperties:
    """Properties of SupplementEngine.calculate_ice_and_water_rolls."""

    @given(
        pitch=pitch_st,
        eave=lf_st,
        valley=lf_st,
    )
    @settings(max_examples=200)
    def test_rolls_always_non_negative(self, pitch, eave, valley):
        """Result is always >= 0 for any valid non-negative inputs."""
        result = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)
        assert result >= 0

    @given(
        pitch=pitch_st,
        eave=lf_st,
        valley=lf_st,
    )
    @settings(max_examples=200)
    def test_rolls_always_integer(self, pitch, eave, valley):
        """Result is always an integer (math.ceil output)."""
        result = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)
        assert isinstance(result, int)

    @given(
        pitch=pitch_st,
        eave=lf_st,
        valley=lf_st,
    )
    @settings(max_examples=200)
    def test_zero_eave_zero_valley_gives_zero_rolls(self, pitch, eave, valley):
        """When eave=0 and valley=0 the total sqft is 0 so rolls == 0."""
        result = SupplementEngine.calculate_ice_and_water_rolls(pitch, 0.0, 0.0)
        assert result == 0

    @given(
        pitch=pitch_st,
        eave=st.floats(min_value=1.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
        valley=lf_st,
        extra_eave=st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_more_eave_never_fewer_rolls(self, pitch, eave, valley, extra_eave):
        """Adding eave footage never decreases roll count (monotone)."""
        base = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)
        bigger = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave + extra_eave, valley)
        assert bigger >= base

    @given(
        pitch=pitch_st,
        eave=lf_st,
        valley=st.floats(min_value=1.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
        extra_valley=st.floats(min_value=0.1, max_value=500.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_more_valley_never_fewer_rolls(self, pitch, eave, valley, extra_valley):
        """Adding valley footage never decreases roll count (monotone)."""
        base = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)
        bigger = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley + extra_valley)
        assert bigger >= base

    @given(
        pitch=st.floats(min_value=-1.0, max_value=-0.01, allow_nan=False, allow_infinity=False),
        eave=lf_st,
        valley=lf_st,
    )
    @settings(max_examples=50)
    def test_negative_pitch_raises(self, pitch, eave, valley):
        """Negative pitch must raise ValueError."""
        with pytest.raises(ValueError):
            SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)

    @given(
        pitch=pitch_st,
        eave=st.floats(min_value=-1.0, max_value=-0.01, allow_nan=False, allow_infinity=False),
        valley=lf_st,
    )
    @settings(max_examples=50)
    def test_negative_eave_raises(self, pitch, eave, valley):
        """Negative eave length must raise ValueError."""
        with pytest.raises(ValueError):
            SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)

    @given(
        pitch=pitch_st,
        eave=lf_st,
        valley=lf_st,
    )
    @settings(max_examples=200)
    def test_result_matches_manual_formula(self, pitch, eave, valley):
        """Roll count must match the expected formula: ceil(total_sqft / ROLL_SQFT_YIELD)."""
        overhang_in = 12.0
        wall_thickness_in = 6.0
        total_horizontal_in = overhang_in + wall_thickness_in + 24.0
        rise_in = (total_horizontal_in / 12.0) * pitch
        sloped_distance_ft = math.hypot(total_horizontal_in, rise_in) / 12.0
        eave_sqft = eave * sloped_distance_ft
        valley_sqft = valley * 3.0
        expected = math.ceil((eave_sqft + valley_sqft) / ROLL_SQFT_YIELD)

        result = SupplementEngine.calculate_ice_and_water_rolls(pitch, eave, valley)
        assert result == expected


# ---------------------------------------------------------------------------
# Shingle waste factor properties
# ---------------------------------------------------------------------------


class TestWasteFactorProperties:
    """Properties of SupplementEngine.evaluate_shingle_waste."""

    @given(
        valley=st.floats(min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False),
        hips=hips_st,
        carrier_waste=st.floats(min_value=0.0, max_value=14.99, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_complex_roof_with_low_carrier_waste_is_flagged(
        self, valley, hips, carrier_waste
    ):
        """Any complex roof (valley>0 OR hips>1) with carrier_waste < 15% must return True."""
        result = SupplementEngine.evaluate_shingle_waste(carrier_waste, valley, hips)
        assert result is True

    @given(
        carrier_waste=st.floats(min_value=15.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        valley=lf_st,
        hips=hips_st,
    )
    @settings(max_examples=200)
    def test_adequate_carrier_waste_never_flagged(self, carrier_waste, valley, hips):
        """If carrier already allows >= 15% waste, the engine must return False."""
        result = SupplementEngine.evaluate_shingle_waste(carrier_waste, valley, hips)
        assert result is False

    @given(
        carrier_waste=st.floats(min_value=0.0, max_value=14.99, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_simple_roof_zero_valley_zero_hips_not_flagged(self, carrier_waste):
        """A simple roof (valley=0, hips=0) must NOT be flagged even with low waste."""
        result = SupplementEngine.evaluate_shingle_waste(carrier_waste, 0.0, 0)
        assert result is False

    @given(
        carrier_waste=st.floats(min_value=0.0, max_value=14.99, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_one_hip_not_flagged(self, carrier_waste):
        """Exactly 1 hip is NOT complex per the engine spec (hips_count > 1 required)."""
        result = SupplementEngine.evaluate_shingle_waste(carrier_waste, 0.0, 1)
        assert result is False


# ---------------------------------------------------------------------------
# Multi-trade O&P properties
# ---------------------------------------------------------------------------


class TestMultiTradeProperties:
    """Properties of SupplementEngine.evaluate_multi_trade_op."""

    @given(
        trades=st.lists(
            st.sampled_from(["RFG", "SFG", "PNT", "SDG", "HVC"]),
            min_size=3,
            max_size=5,
            unique=True,
        )
    )
    @settings(max_examples=100)
    def test_three_or_more_distinct_trades_qualifies(self, trades):
        """3 or more distinct recognized trades must return True."""
        items = [{"category": t, "quantity": 1} for t in trades]
        assert SupplementEngine.evaluate_multi_trade_op(items) is True

    @given(
        trade=st.sampled_from(["RFG", "SFG", "PNT", "SDG", "HVC"]),
        count=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=100)
    def test_single_trade_repeated_does_not_qualify(self, trade, count):
        """Repeating a single trade N times must return False — uniqueness matters."""
        items = [{"category": trade, "quantity": i} for i in range(count)]
        assert SupplementEngine.evaluate_multi_trade_op(items) is False

    def test_empty_items_returns_false(self):
        """An empty item list must return False."""
        assert SupplementEngine.evaluate_multi_trade_op([]) is False

    def test_unknown_trade_codes_ignored(self):
        """Items with unrecognized trade codes do not contribute to count."""
        items = [{"category": "ZZZ"}, {"category": "AAA"}, {"category": "BBB"}]
        assert SupplementEngine.evaluate_multi_trade_op(items) is False
