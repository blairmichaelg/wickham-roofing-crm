"""
Deterministic Xactimate & Geometric Engine.
Handles exact mathematical evaluations and parses ESX archives.
"""
import math

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
        """
        is_complex = valley_length_ft > 0 or hips_count > 1
        return is_complex and carrier_waste_pct < 15.0
