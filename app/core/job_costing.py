"""
V4 Job Costing Engine.
Pure Python domain logic for calculating job profitability and margins.
"""

from __future__ import annotations


def compute_job_profitability(
    revenue_cents: int, 
    materials_cents: int, 
    labor_cents: int, 
    overhead_pct: float, 
    commission_pct: float = 0.10,
    commission_pct_override: float | None = None,
) -> dict[str, int | float]:
    """Computes precise industry financial metrics before a build begins.

    Args:
        revenue_cents (int): The total contract price (Carrier RCV) in cents.
        materials_cents (int): The total calculated material cost in cents.
        labor_cents (int): The total calculated labor cost in cents.
        overhead_pct (float): The baseline overhead percentage (e.g., 0.10).
        commission_pct (float): The canvasser commission percentage (e.g., 0.10).
        commission_pct_override (float | None): Optional manual commission override (e.g., 0.15).

    Returns:
        dict[str, int | float]: A dictionary containing direct_costs_cents, gross_profit_cents, 
            gross_margin, overhead_cost_cents, net_profit_cents, canvasser_commission_cents, 
            and effective_commission_pct.
    """
    direct_costs_cents = materials_cents + labor_cents
    gross_profit_cents = revenue_cents - direct_costs_cents
    
    # Handle zero division safety
    if revenue_cents > 0:
        gross_margin = gross_profit_cents / revenue_cents
    else:
        gross_margin = 0.0
        
    overhead_cost_cents = int(round(revenue_cents * overhead_pct))
    net_profit_cents = gross_profit_cents - overhead_cost_cents
    
    effective_commission_pct = (
        commission_pct_override if commission_pct_override is not None else commission_pct
    )
    canvasser_commission_cents = int(round(revenue_cents * effective_commission_pct))
    
    return {
        "direct_costs_cents": direct_costs_cents,
        "gross_profit_cents": gross_profit_cents,
        "gross_margin": round(gross_margin, 4),
        "overhead_cost_cents": overhead_cost_cents,
        "net_profit_cents": net_profit_cents,
        "canvasser_commission_cents": canvasser_commission_cents,
        "effective_commission_pct": effective_commission_pct
    }
