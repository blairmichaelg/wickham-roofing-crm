"""
Pure deterministic geometry validation for manual measurement entry and PDF parsing sanity.
Zero AI, zero database dependencies, pure mathematical proofs and geometric boundaries.
"""

import math

# Standard rise/12 pitch multipliers: sqrt(rise^2 + 144) / 12
PITCH_MULTIPLIERS: dict[int, float] = {
    rise: round(math.sqrt(rise**2 + 144) / 12.0, 4) for rise in range(1, 25)
}


def get_pitch_multiplier(pitch_value: float) -> float:
    """Calculate the exact continuous pitch multiplier for any rise."""
    if pitch_value <= 0:
        return 1.0
    return math.sqrt(pitch_value**2 + 144.0) / 12.0


def validate_pitch(pitch: str | float | int) -> float:
    """
    Parse pitch string (e.g. '6/12', '7.5/12', '8') and validate that rise is between 1 and 24.
    Returns the numeric rise value (e.g., 6.0).
    Raises ValueError on malformed or out-of-range input.
    """
    if pitch is None:
        raise ValueError("Pitch must not be None.")

    if isinstance(pitch, (int, float)):
        rise = float(pitch)
    elif isinstance(pitch, str):
        cleaned = pitch.strip()
        if not cleaned:
            raise ValueError("Pitch string cannot be empty.")
        if "/" in cleaned:
            parts = cleaned.split("/")
            if len(parts) != 2:
                raise ValueError(f"Malformed pitch format '{pitch}'. Expected 'rise/12'.")
            try:
                rise = float(parts[0].strip())
                run = float(parts[1].strip())
            except ValueError:
                raise ValueError(f"Invalid numeric components in pitch '{pitch}'.")
            if run != 12.0:
                raise ValueError(f"Standard roof pitch run must be 12, got {run}.")
        else:
            try:
                rise = float(cleaned)
            except ValueError:
                raise ValueError(f"Invalid pitch string '{pitch}'.")
    else:
        raise ValueError(f"Unsupported pitch type: {type(pitch).__name__}")

    if rise < 1.0 or rise > 24.0:
        raise ValueError(f"Pitch rise {rise} is out of standard physical range (1 to 24).")

    return round(rise, 2)


def validate_area_to_footprint(
    total_area_sf: float,
    pitch_multiplier: float,
    perimeter_lf: float
) -> None:
    """
    Geometric impossibility check:
    For any closed 2D plane with perimeter P, maximum possible horizontal footprint area is (P / 4)^2 (a square).
    The horizontal footprint of a roof is (total_area_sf / pitch_multiplier).
    If footprint exceeds (perimeter_lf / 4)^2, the geometry is physically impossible.
    Raises ValueError on impossible geometry.
    """
    if total_area_sf < 0:
        raise ValueError("Total roof area cannot be negative.")
    if perimeter_lf < 0:
        raise ValueError("Perimeter cannot be negative.")

    if total_area_sf == 0:
        return

    if perimeter_lf == 0:
        raise ValueError("Non-zero roof area requires a non-zero perimeter.")

    if pitch_multiplier <= 0:
        pitch_multiplier = 1.0

    horizontal_footprint = total_area_sf / pitch_multiplier
    max_possible_footprint = (perimeter_lf / 4.0) ** 2

    # Allow a small 5% margin for measurement rounding / overhang variance
    if horizontal_footprint > max_possible_footprint * 1.05:
        raise ValueError(
            f"Impossible geometry: horizontal footprint ({horizontal_footprint:.1f} sq ft) "
            f"exceeds maximum theoretical area for perimeter {perimeter_lf:.1f} LF "
            f"({max_possible_footprint:.1f} sq ft)."
        )


def validate_edge_completeness(
    total_area_sf: float,
    eaves_lf: float,
    rake_lf: float,
    ridge_lf: float
) -> None:
    """
    Validates that a roof with non-zero area has at least one enclosing or ridge edge.
    Raises ValueError if total_area_sf > 0 but eaves_lf + rake_lf + ridge_lf == 0.
    """
    if total_area_sf > 0 and (eaves_lf + rake_lf + ridge_lf) <= 0:
        raise ValueError(
            f"Incomplete edge data: roof has {total_area_sf:.1f} sq ft but eaves, "
            "rakes, and ridge length sum to zero."
        )


def requires_steep_charge(pitch_value: float) -> bool:
    """
    Returns True if pitch_value >= 7.0 (rise >= 7/12).
    Matches the RFG STEEP threshold in supplement_engine.py.
    """
    return pitch_value >= 7.0
