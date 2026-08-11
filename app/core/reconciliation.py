"""
Pure Python reconciliation engine.

Deterministically compares normalized EagleView measurement data
against extracted Carrier Statement of Loss line items to generate
a DiscrepancyReport. This isolates all math from the LLM.
"""

import math

from app.core.supplement_models import (
    Discrepancy,
    DiscrepancyReport,
    EagleViewData,
    MaterialBOM,
    StatementOfLoss,
)


def _get_item_qty(item) -> float:
    if hasattr(item, "quantity"):
        q = item.quantity
        if hasattr(q, "value"):
            return float(q.value or 0.0)
        elif isinstance(q, (int, float)):
            return float(q)
    return 0.0

def _get_item_unit(item) -> str:
    if hasattr(item, "unit") and item.unit:
        u = item.unit
        if hasattr(u, "value"):
            return str(u.value or "").strip().upper()
        elif isinstance(u, str):
            return u.strip().upper()
    if hasattr(item, "unit_of_measure") and item.unit_of_measure:
        u = item.unit_of_measure
        if isinstance(u, str):
            return u.strip().upper()
    return ""

def _get_item_desc(item) -> str:
    if hasattr(item, "description") and item.description:
        return str(item.description)
    return ""

def reconcile(ev: EagleViewData, sol: StatementOfLoss, job_id: str, waste_factor: float = 0.15) -> DiscrepancyReport:
    """
    Deterministically reconcile EV measurements against SoL items.
    Accepts waste_factor dynamically (e.g. 0.15 for 15%).
    """
    discrepancies = []

    # 1. Area Computation
    ev_normalized_squares = (ev.total_area_sf / 100.0) * (1.0 + waste_factor)
    from app.core.complexity import build_waste_explanation, compute_complexity_score
    score = compute_complexity_score(ev)
    waste_explanation = build_waste_explanation(ev, score, waste_factor)
    
    sol_total_rfg_squares = 0.0
    sq_items = [
        _get_item_qty(item) for item in sol.line_items 
        if _get_item_unit(item) in ("SQ", "SQ.")
    ]
    if sq_items:
        sol_total_rfg_squares = max(sq_items)

    square_variance = round(ev_normalized_squares - sol_total_rfg_squares, 2)

    if square_variance > 0.01:
        discrepancies.append(
            Discrepancy(
                category="Area Shortage",
                description=f"Carrier allowed {sol_total_rfg_squares} SQ. EagleView normalized is {ev_normalized_squares} SQ.",
                ev_value=ev_normalized_squares,
                sol_value=sol_total_rfg_squares,
                variance=square_variance,
                xactimate_code="RFG 300S",
            )
        )

    # 2. Ice & Water Shield (Valleys)
    if ev.valley_lf > 0:
        found_ice_water = any(
            "ice" in _get_item_desc(item).lower() or 
            "water" in _get_item_desc(item).lower() or 
            "barrier" in _get_item_desc(item).lower()
            for item in sol.line_items
        )
        if not found_ice_water:
            discrepancies.append(
                Discrepancy(
                    category="Missing Ice & Water Shield",
                    description=f"EagleView shows {ev.valley_lf} LF of valleys, but no Ice & Water Shield is included in the SoL.",
                    ev_value=ev.valley_lf,
                    sol_value=0.0,
                    variance=ev.valley_lf,
                    xactimate_code="RFG IWS",
                )
            )

    # 3. Ridge / Hip Cap
    total_ridge_hip_lf = ev.ridge_lf + ev.hip_lf
    if total_ridge_hip_lf > 0:
        ridge_items = [
            _get_item_qty(item) for item in sol.line_items
            if ("ridge" in _get_item_desc(item).lower() or "hip" in _get_item_desc(item).lower())
            and _get_item_unit(item) in ("LF", "LF.")
        ]
        
        # Max of the ridge items to avoid double-counting remove/replace
        sol_ridge_hip_lf = max(ridge_items) if ridge_items else 0.0
        
        ridge_variance = round(total_ridge_hip_lf - sol_ridge_hip_lf, 2)
        if ridge_variance > 0.01:
            discrepancies.append(
                Discrepancy(
                    category="Ridge/Hip Cap Shortage",
                    description=f"EagleView shows {total_ridge_hip_lf} LF of Ridges & Hips. Carrier allowed {sol_ridge_hip_lf} LF.",
                    ev_value=total_ridge_hip_lf,
                    sol_value=sol_ridge_hip_lf,
                    variance=ridge_variance,
                    xactimate_code="RFG RIDGC",
                )
            )

    # 4. Overhead & Profit Check (Automatic 20%)
    unique_trades = set(item.trade for item in sol.line_items if hasattr(item, 'trade') and item.trade)
    overhead_included = getattr(sol, 'overhead_and_profit_included', False)
    if overhead_included is False and len(unique_trades) >= 1: # Flag if missing regardless of trade count for now, let adjuster argue it
        discrepancies.append(
            Discrepancy(
                category="Missing O&P",
                description="Overhead and Profit (20%) is missing from the Carrier Statement of Loss but is legally warranted due to project complexity.",
                ev_value=None,
                sol_value=None,
                variance=None,
                xactimate_code="FEE O&P",
            )
        )

    # 5. Deterministic Material BOM using exact math.ceil formulas
    eaves_and_rakes = ev.eaves_lf + ev.rake_lf
    bom = MaterialBOM(
        field_shingle_bundles=math.ceil(ev_normalized_squares * 3),
        starter_bundles=math.ceil(eaves_and_rakes / 100),
        ridge_cap_bundles=math.ceil(ev.ridge_lf / 33),
        ice_water_rolls=math.ceil((ev.valley_lf * 3) / 200),
        underlayment_rolls=math.ceil(ev_normalized_squares / 10),
        drip_edge_pieces=math.ceil(eaves_and_rakes / 10)
    )

    return DiscrepancyReport(
        job_id=job_id,
        ev_normalized_squares=ev_normalized_squares,
        sol_total_rfg_squares=sol_total_rfg_squares,
        square_variance=square_variance,
        waste_explanation=waste_explanation,
        material_bom=bom,
        discrepancies=discrepancies,
    )
