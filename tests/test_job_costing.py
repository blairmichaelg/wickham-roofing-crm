"""
Unit tests for the V4 Job Costing math engine (Integer Cents).
"""

from app.core.job_costing import compute_job_profitability


def test_compute_job_profitability_normal_case():
    """Test standard profitability calculations."""
    results = compute_job_profitability(
        revenue_cents=1000000,
        materials_cents=300000,
        labor_cents=200000,
        overhead_pct=0.25,
        commission_pct=0.10
    )
    
    assert results["direct_costs_cents"] == 500000
    assert results["gross_profit_cents"] == 500000
    assert results["gross_margin"] == 0.5000
    assert results["overhead_cost_cents"] == 250000
    assert results["net_profit_cents"] == 250000
    assert results["canvasser_commission_cents"] == 100000

def test_compute_job_profitability_low_margin():
    """Test when gross margin drops below 35%."""
    results = compute_job_profitability(
        revenue_cents=1000000,
        materials_cents=450000,
        labor_cents=250000,
        overhead_pct=0.20,
        commission_pct=0.10
    )
    
    assert results["direct_costs_cents"] == 700000
    assert results["gross_profit_cents"] == 300000
    assert results["gross_margin"] == 0.3000
    assert results["overhead_cost_cents"] == 200000
    assert results["net_profit_cents"] == 100000
    assert results["canvasser_commission_cents"] == 100000

def test_compute_job_profitability_zero_division_safeguard():
    """Test zero division safeguard when revenue is 0."""
    results = compute_job_profitability(
        revenue_cents=0,
        materials_cents=300000,
        labor_cents=200000,
        overhead_pct=0.25,
        commission_pct=0.10
    )
    
    assert results["direct_costs_cents"] == 500000
    assert results["gross_profit_cents"] == -500000
    assert results["gross_margin"] == 0.0
    assert results["overhead_cost_cents"] == 0
    assert results["net_profit_cents"] == -500000
    assert results["canvasser_commission_cents"] == 0

def test_compute_job_profitability_float_precision_drift():
    """Test that integer cents prevents precision drift on repeating decimals.
    
    Historically, multiplying 1000.1 by 0.1 might yield 100.01000000000001,
    but by passing cents we enforce exact integer operations.
    """
    # E.g. $10,000.10 revenue = 1000010 cents
    # Overhead 0.1, commission 0.1
    # 1000010 * 0.1 = 100001
    results = compute_job_profitability(
        revenue_cents=1000010,
        materials_cents=0,
        labor_cents=0,
        overhead_pct=0.10,
        commission_pct=0.10
    )
    
    # 1000010 * 0.10 is exactly 100001.0, rounded -> 100001 cents
    assert results["overhead_cost_cents"] == 100001
    assert results["canvasser_commission_cents"] == 100001
    # Net profit: 1000010 - 100001 = 900009
    assert results["net_profit_cents"] == 900009
