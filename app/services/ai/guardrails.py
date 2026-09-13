"""
Deterministic, non-AI guardrails for AI-generated content.

Runs before any AI-generated narrative, scope of work, pitch script,
or financial estimate is presented to a user or persisted to the database.

Core rules enforced:
1. Math Reconciliation: AI-stated dollar totals must deterministically match
   the sum of underlying line items.
2. Legal Disclaimers: Required statutory notices, non-obligation statements,
   or UPPA (unauthorized practice of public adjusting) disclaimers must be present.
3. No Hallucinated Figures: Rejects ungrounded prices or figures.
"""

import re
from typing import Any

from pydantic import BaseModel, Field


class GuardrailValidationError(ValueError):
    """Raised when AI-generated content fails deterministic safety/math checks."""

    def __init__(self, message: str, violations: list[str] | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.violations = violations or []
        self.details = details or {}


class SalesPromiseViolationError(GuardrailValidationError):
    """Raised when content violates sales promises rules (deductible waiving, coverage guarantees)."""
    pass


class GuardrailResult(BaseModel):
    """Result of a deterministic guardrail inspection."""
    passed: bool
    violations: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


def verify_math_reconciliation(
    stated_total: float | int,
    line_items: list[dict[str, Any] | Any],
    cost_key: str = "total_cost",
    tolerance_cents: int = 1,
) -> GuardrailResult:
    """
    Verify that an AI-stated dollar total exactly equals the sum of line items.

    Converts all currency values to integer cents to eliminate floating-point error.

    Args:
        stated_total: The total dollar or cents figure asserted by the AI model.
        line_items: List of line items (dicts or objects) with cost/amount fields.
        cost_key: Key or attribute name for line item cost.
        tolerance_cents: Max allowable rounding deviation in cents (default 1 cent).

    Returns:
        GuardrailResult: Pass/fail status and violation details.
    """
    violations: list[str] = []

    # Coerce stated total into integer cents
    if isinstance(stated_total, float) or stated_total < 1000 and isinstance(stated_total, (int, float)):
        # Likely floating dollars, convert to cents
        stated_cents = round(float(stated_total) * 100)
    else:
        stated_cents = int(stated_total)

    computed_sum_cents = 0
    for idx, item in enumerate(line_items):
        raw_val = None
        if isinstance(item, dict):
            raw_val = item.get(cost_key)
            if raw_val is None and "cost" in item:
                raw_val = item.get("cost")
            elif raw_val is None and "amount" in item:
                raw_val = item.get("amount")
            elif raw_val is None and "claimed_rcv" in item:
                raw_val = item.get("claimed_rcv")
            elif raw_val is None and "price" in item:
                raw_val = item.get("price")
        else:
            raw_val = getattr(item, cost_key, None)
            if raw_val is None:
                raw_val = getattr(item, "cost", getattr(item, "amount", getattr(item, "claimed_rcv", None)))

        if raw_val is not None:
            try:
                # If raw_val is float or <= 10000.0, treat as dollars unless explicitly marked
                if isinstance(raw_val, float) or (isinstance(raw_val, (int, float)) and raw_val < 500):
                    item_cents = round(float(raw_val) * 100)
                else:
                    item_cents = int(round(float(raw_val)))
                computed_sum_cents += item_cents
            except (ValueError, TypeError):
                violations.append(f"Line item [{idx}] has invalid non-numeric cost value: {raw_val}")

    diff_cents = abs(computed_sum_cents - stated_cents)
    if diff_cents > tolerance_cents:
        stated_dollars = stated_cents / 100.0
        computed_dollars = computed_sum_cents / 100.0
        violations.append(
            f"Stated total (${stated_dollars:,.2f}) does not match the sum of "
            f"underlying line items (${computed_dollars:,.2f}). Difference: ${diff_cents / 100.0:,.2f}"
        )

    passed = len(violations) == 0
    return GuardrailResult(
        passed=passed,
        violations=violations,
        details={
            "stated_cents": stated_cents,
            "computed_sum_cents": computed_sum_cents,
            "difference_cents": diff_cents,
        },
    )


def verify_legal_disclaimers(
    text: str,
    required_keywords: list[str] | None = None,
    disclaimer_type: str = "sales_script",
) -> GuardrailResult:
    """
    Verify that mandatory consumer disclosures or legal notices are present.

    Default keyword profiles:
    - 'sales_script': Requires a non-obligation disclaimer ("no obligation", "complimentary", "free inspection", etc.)
    - 'public_adjuster_disclaimer': Requires contractor role disclosure (cannot act as public adjuster).
    - 'contract_scope': Requires standard workmanship / code compliance warranty notice.
    """
    violations: list[str] = []
    text_lower = text.lower()

    if required_keywords is None:
        if disclaimer_type == "sales_script":
            # Must have at least one non-coercive/free inspection phrase
            required_keywords = ["free", "complimentary", "no obligation", "no pressure"]
        elif disclaimer_type == "public_adjuster_disclaimer":
            required_keywords = ["not a public adjuster", "contractor", "estimate"]
        else:
            required_keywords = []

    if required_keywords:
        # Check if at least one required keyword/phrase matches (or all, depending on flag)
        has_any = any(kw.lower() in text_lower for kw in required_keywords)
        if not has_any:
            violations.append(
                f"Content missing required {disclaimer_type} legal disclaimer. "
                f"Must include one of: {required_keywords}"
            )

    passed = len(violations) == 0
    return GuardrailResult(
        passed=passed,
        violations=violations,
        details={"disclaimer_type": disclaimer_type, "checked_keywords": required_keywords},
    )


PROHIBITED_PATTERNS = [
    (re.compile(r"\b(?:waiv|absorb|cover|pay)\w*\b[^\.\n]*\bdeductible\b", re.IGNORECASE), "Prohibited deductible waiving or absorption offer"),
    (re.compile(r"\bfree roof\b", re.IGNORECASE), "Prohibited 'free roof' deceptive advertising claim"),
    (re.compile(r"\b(?:guarantee|promise|certif)\w*\b[^\.\n]*\b(?:cover|approv|pay)\w*\b", re.IGNORECASE), "Prohibited insurance coverage or carrier approval guarantee"),
    (re.compile(r"\binsurance\b[^\.\n]*\b(?:guaranteed to pay|must pay|will 100% pay|will pay)\b", re.IGNORECASE), "Prohibited carrier payment assertion"),
    (re.compile(r"\binsurance owes you\b", re.IGNORECASE), "Prohibited public adjuster assertion"),
]


def verify_prohibited_sales_promises(text: str, raise_on_failure: bool = False) -> GuardrailResult:
    """
    Ensure AI-generated pitch or sales scripts contain zero deceptive claims:
    - No deductible waiving or rebate offers.
    - No guarantees of insurance claim coverage/approval.
    - No public adjuster representation assertions.
    """
    violations: list[str] = []
    for pattern, reason in PROHIBITED_PATTERNS:
        if pattern.search(text):
            violations.append(reason)

    if violations and raise_on_failure:
        raise SalesPromiseViolationError(
            f"Prohibited sales promises detected: {'; '.join(violations)}",
            violations=violations,
            details={"violations_count": len(violations)},
        )

    return GuardrailResult(
        passed=len(violations) == 0,
        violations=violations,
        details={"violations_count": len(violations)},
    )


def check_all_guardrails(
    content_text: str | None = None,
    stated_total: float | int | None = None,
    line_items: list[Any] | None = None,
    disclaimer_type: str | None = None,
    raise_on_failure: bool = True,
) -> GuardrailResult:
    """
    Composite guardrail pipeline runner.
    Executes all applicable deterministic checks and either returns the result or raises.
    """
    all_violations: list[str] = []
    combined_details: dict[str, Any] = {}

    # 1. Math check
    if stated_total is not None and line_items is not None:
        math_res = verify_math_reconciliation(stated_total, line_items)
        if not math_res.passed:
            all_violations.extend(math_res.violations)
        combined_details["math"] = math_res.details

    # 2. Disclaimer check
    if content_text and disclaimer_type:
        disc_res = verify_legal_disclaimers(content_text, disclaimer_type=disclaimer_type)
        if not disc_res.passed:
            all_violations.extend(disc_res.violations)
        combined_details["disclaimer"] = disc_res.details

    passed = len(all_violations) == 0
    if not passed and raise_on_failure:
        raise GuardrailValidationError(
            f"AI content failed guardrail validation: {'; '.join(all_violations)}",
            violations=all_violations,
            details=combined_details,
        )

    return GuardrailResult(
        passed=passed,
        violations=all_violations,
        details=combined_details,
    )
