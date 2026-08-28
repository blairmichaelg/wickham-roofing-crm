"""
Deterministic Xactimate & Geometric Engine.
Handles exact mathematical evaluations and parses ESX archives.
"""
import math
from typing import Any

# Constant for IRC IWS calculation
ROLL_SQFT_YIELD = 66.7
# Standard Xactimate Trade Categories
TRADES = {"RFG", "SFG", "PNT", "SDG", "HVC"}

class SupplementEngine:
    """SupplementEngine definition."""
    @staticmethod
    def parse_esx(file_path: str) -> list[dict[str, str | float]]:
        """
        Extract code, category, and quantity from an Xactimate .ESX archive.
        ESX is a ZIP file containing an estimate.xml.
        """
        raise NotImplementedError(
            "ESX parsing is retired as of Phase 2 hardening. "
            "Adjusters in this market use Xactimate PDF exports (SoL), "
            "not raw .ESX archives. Use the SoL PDF ingestion pipeline. "
            "To re-enable, implement against the actual Xactimate XML "
            "schema v28+ spec before removing this guard."
        )
        
    @staticmethod
    def evaluate_multi_trade_op(items: list[dict[str, str | float]]) -> bool:
        """
        Evaluate if Overhead & Profit should be applied based on >= 3 distinct trades.
        """
        unique_trades = set()
        for item in items:
            cat = str(item.get("category", "")).upper()
            if cat in TRADES:
                unique_trades.add(cat)
        
        return len(unique_trades) >= 3
        
    @staticmethod
    def calculate_ice_and_water_rolls(
        pitch: float, 
        eave_length_ft: float, 
        valley_length_ft: float, 
        wall_thickness_in: float = 6.0, 
        overhang_in: float = 12.0
    ) -> int:
        """
        Calculates IWS rolls required based on 2021/2024 IRC R905.1.2.
        Barrier must extend 24" horizontally inside the interior wall line.
        """
        pitch = float(pitch)
        eave_length_ft = float(eave_length_ft)
        valley_length_ft = float(valley_length_ft)

        if pitch < 0 or eave_length_ft < 0 or valley_length_ft < 0:
            import structlog
            logger = structlog.get_logger("app.services.supplement_engine")
            logger.warning("invalid_iws_inputs", pitch=pitch, eave=eave_length_ft, valley=valley_length_ft)
            raise ValueError(f"Malformed EagleView inputs: pitch={pitch}, eave={eave_length_ft}, valley={valley_length_ft}")
        
        # Horizontal distance required in inches: overhang + wall + 24" inside
        total_horizontal_in = overhang_in + wall_thickness_in + 24.0
        
        # Calculate hypotenuse (sloped distance) in inches
        # Pitch is X/12 (X inches rise per 12 inches run)
        rise_in = (total_horizontal_in / 12.0) * pitch
        sloped_distance_in = math.hypot(total_horizontal_in, rise_in)
        
        sloped_distance_ft = sloped_distance_in / 12.0
        
        # Total square footage for eaves
        eave_sqft = eave_length_ft * sloped_distance_ft
        
        # Standard valley coverage is 36" (3 ft) wide
        valley_sqft = valley_length_ft * 3.0
        
        total_sqft = eave_sqft + valley_sqft
        rolls = math.ceil(total_sqft / ROLL_SQFT_YIELD)
        
        return rolls
        
    @staticmethod
    def evaluate_shingle_waste(carrier_waste_pct: float, valley_length_ft: float, hips_count: int) -> bool:
        """
        Returns True if complex geometry dictates a 15% waste factor 
        and the carrier estimate defaulted to < 15% (e.g. 10%).
        NOTE: Conservative baseline formula; requires empirical validation against accepted carrier supplements.
        """
        is_complex = valley_length_ft > 0 or hips_count > 1
        return is_complex and carrier_waste_pct < 15.0

    @staticmethod
    def evaluate_steep_charge(pitch: float | str) -> tuple[bool, float]:
        """
        Evaluates RFG STEEP line-item eligibility.
        Triggers when roof pitch is >= 7/12 (i.e. rise >= 7.0 inches per 12 inches run).
        Returns (is_steep: bool, parsed_pitch: float).
        """
        if isinstance(pitch, str):
            try:
                pitch_num = float(pitch.split("/")[0].strip())
            except (ValueError, IndexError):
                pitch_num = 0.0
        else:
            pitch_num = float(pitch)
        
        is_steep = pitch_num >= 7.0
        return is_steep, pitch_num

    @staticmethod
    def evaluate_ridge_cap_upgrade(shingle_type: str | None) -> tuple[bool, str]:
        """
        Evaluates RFG RIDGC+ (High-Profile Architectural Ridge Cap) vs standard RFG RIDGC.
        Triggers upgrade when shingle_type indicates dimensional/architectural shingles.
        Returns (is_upgrade: bool, target_code: str).
        """
        if not shingle_type:
            return False, "RFG RIDGC"
        
        st_lower = shingle_type.strip().lower()
        architectural_keywords = ["architectural", "dimensional", "laminate", "timberline", "duration", "landmark", "pro", "hdx"]
        is_arch = any(kw in st_lower for kw in architectural_keywords)
        
        if is_arch:
            return True, "RFG RIDGC+"
        return False, "RFG RIDGC"

    @staticmethod
    def evaluate_gutter_replacement(damage_signals: list[Any] | str | None = None, gutters_damaged: bool = False) -> bool:
        """
        Evaluates SFG GUTA (Seamless Gutter Replacement) line-item eligibility.
        Triggers ONLY when gutter hail damage or denting is explicitly documented in inspection data.
        Does not auto-add speculatively.
        """
        if gutters_damaged:
            return True
        
        if not damage_signals:
            return False
        
        gutter_keywords = ["gutter", "downspout", "sfg_guta", "gutter_damage", "gutter_dent", "gutter_impact"]
        
        if isinstance(damage_signals, str):
            ds_lower = damage_signals.lower()
            return any(kw in ds_lower for kw in gutter_keywords)
        
        if isinstance(damage_signals, list):
            for item in damage_signals:
                if isinstance(item, str):
                    if any(kw in item.lower() for kw in gutter_keywords):
                        return True
                elif isinstance(item, dict):
                    # Check signal name, type, or notes
                    for v in item.values():
                        if isinstance(v, str) and any(kw in v.lower() for kw in gutter_keywords):
                            return True
        return False

    @staticmethod
    def evaluate_dumpster_hauloff(tear_off_required: bool = True, squares: float = 0.0) -> tuple[bool, int]:
        """
        Evaluates DMO DUMP (Dumpster Haul-Off & Disposal Container Fee).
        Included by default on every tear-off restoration job.
        Calculates container count: 1 container per 30 squares, minimum 1 container on tear-off.
        Returns (is_required: bool, container_count: int).
        """
        if not tear_off_required:
            return False, 0
        
        count = max(1, math.ceil(squares / 30.0)) if squares > 0 else 1
        return True, count

    @staticmethod
    def evaluate_decking_renail(tear_off_required: bool = True, is_high_wind_zone: bool = True) -> bool:
        """
        Evaluates RFG RENAIL (Re-Nail Roof Deck Sheathing) line item per IRC R905.2.1.
        Triggered on full tear-off restorations to ensure decking fastening meets modern code.
        """
        return tear_off_required
