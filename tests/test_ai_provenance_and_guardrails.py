"""
tests/test_ai_provenance_and_guardrails.py — Tests for Phase E Grounded AI Provenance & Guardrails.

Verifies:
1. Negative guardrail: blocks deductible waiving and insurance coverage promises.
2. Sales narrative provenance: outputs verified grounding context and disclaimer.
3. Code router fallback: returns neutral 'manual review required' state when ungrounded.
"""

from __future__ import annotations

import pytest

from app.services.ai.guardrails import (
    verify_prohibited_sales_promises,
    SalesPromiseViolationError,
)
from app.services.sales_narrative import build_sales_provenance
from app.core.code_router import get_relevant_codes


def test_guardrail_blocks_deductible_waiving():
    """Verify negative guardrail blocks prohibited deductible absorption / waiving promises."""
    prohibited_text_1 = "Sign with us today and we will waive your deductible completely!"
    prohibited_text_2 = "Don't worry about the $1,000 deductible, our rebate covers your deductible in full."
    prohibited_text_3 = "Sign up with Wickham and we'll take care of your deductible!"
    prohibited_text_4 = "Our company will handle your deductible so you pay nothing."
    prohibited_text_5 = "Your deductible will be taken care of through our promotional credit."

    for txt in [prohibited_text_1, prohibited_text_2, prohibited_text_3, prohibited_text_4, prohibited_text_5]:
        with pytest.raises(SalesPromiseViolationError, match="deductible"):
            verify_prohibited_sales_promises(txt, raise_on_failure=True)


def test_guardrail_blocks_free_roof_and_public_adjuster_claims():
    """Verify negative guardrail blocks 'free roof' and public adjuster assertion language."""
    free_roof_text = "Call now to claim your 100% free roof under the new storm program!"
    with pytest.raises(SalesPromiseViolationError, match="free roof"):
        verify_prohibited_sales_promises(free_roof_text, raise_on_failure=True)

    pa_claim_text = "The insurance owes you a full roof replacement under Georgia law."
    with pytest.raises(SalesPromiseViolationError, match="public adjuster"):
        verify_prohibited_sales_promises(pa_claim_text, raise_on_failure=True)


def test_guardrail_blocks_coverage_guarantees():
    """Verify negative guardrail blocks unqualified guarantees that insurance will approve/pay."""
    prohibited_text_1 = "We guarantee that State Farm will approve your roof replacement."
    prohibited_text_2 = "Insurance is 100% guaranteed to pay for this new roof."

    with pytest.raises(SalesPromiseViolationError, match="coverage"):
        verify_prohibited_sales_promises(prohibited_text_1, raise_on_failure=True)

    with pytest.raises(SalesPromiseViolationError, match="coverage"):
        verify_prohibited_sales_promises(prohibited_text_2, raise_on_failure=True)


def test_guardrail_allows_factual_compliant_statements():
    """Verify compliant, factual canvassing and inspection talk track passes the guardrail."""
    compliant_text = (
        "NWS verified 1.75 inch hail in Thomasville on September 11. "
        "We provide complimentary roof damage inspections and assist with documenting forensic evidence for your claim."
    )
    result = verify_prohibited_sales_promises(compliant_text)
    assert result.passed is True


def test_sales_provenance_and_disclaimer():
    """Verify sales provenance block explicitly includes verified storm details and required review disclaimer."""
    job = {
        "address_line1": "789 Pine St",
        "city": "Thomasville",
        "state": "GA",
        "status": "LEAD_CAPTURED",
    }
    storm_events = [
        {
            "event_type": "HAIL",
            "county": "Thomas County",
            "report_time_utc": "2026-09-11 15:30:00",
            "hail_size_inches": 1.75,
        }
    ]
    prov = build_sales_provenance(job, storm_events)

    assert "789 Pine St" in prov["grounded_job_address"]
    assert "AI-assisted draft — verify before use" in prov["disclaimer"]
    assert prov["citation_count"] == 1
    assert "1.75\" hail" in prov["storm_events_cited"][0]["magnitude"]


def test_code_router_ungrounded_fallback():
    """Verify that when no statutory or IRC building code citation exists, the neutral fallback is returned."""
    from app.core.supplement_models import DiscrepancyReport

    report = DiscrepancyReport.model_construct(discrepancies=[])
    result = get_relevant_codes(report, {})
    assert "Manual review required; no supporting statutory or building-code source attached." in result
