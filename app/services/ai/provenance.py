"""
AI Provenance Contract & Enforcement Chokepoint.

Mandatory verification chokepoint wrapping all Gemini AI generation calls
that return free-form text destined for documents, customer-facing surfaces,
sales scripts, or supplement narratives.

Enforces:
1. Zero prohibited sales language (deductible waiving, coverage guarantees, PA claims).
2. Grounded source data reference tracking.
3. Cryptographic prompt version hashing.
4. Guaranteed ProvenanceString return type (never bare str).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import structlog
from pydantic import BaseModel, Field

from app.services.ai.guardrails import (
    CodeCitationViolationError,
    SalesPromiseViolationError,
    verify_building_code_citations,
    verify_prohibited_sales_promises,
)

logger = structlog.get_logger("app.services.ai.provenance")


class ProvenanceString(BaseModel):
    """
    Validated string output with complete AI provenance and audit metadata.

    Guarantees that the wrapped text has successfully passed deterministic
    anti-deception and compliance guardrails before reaching any document.
    """

    text: str
    source_refs: list[str] = Field(default_factory=list)
    guardrail_passed: bool = True
    guardrail_checks: list[str] = Field(default_factory=list)
    model_name: str = "gemini-3.5-flash"
    prompt_version_hash: str = ""

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"ProvenanceString({self.text!r}, guardrail_passed={self.guardrail_passed}, hash={self.prompt_version_hash[:8]!r})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.text == other
        if isinstance(other, ProvenanceString):
            return self.text == other.text and self.guardrail_passed == other.guardrail_passed
        return False

    def __contains__(self, item: str) -> bool:
        return item in self.text

    def __len__(self) -> int:
        return len(self.text)


def enforce_provenance(
    prompt_name: str | None = None,
    checks: list[str] | None = None,
    operation_type: str | None = None,
) -> Callable:
    """
    Async decorator that wraps Gemini text-generation calls returning narrative text.

    Executes the underlying generation, runs deterministic compliance guardrails
    (including verify_prohibited_sales_promises), and wraps the result in a ProvenanceString.

    Raises:
        SalesPromiseViolationError: If any prohibited sales language is detected.
        GuardrailValidationError: If any other deterministic guardrail check fails.
    """
    active_checks = list(checks) if checks is not None else ["verify_prohibited_sales_promises"]

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> ProvenanceString:
            # 1. Execute underlying text generation
            raw_output = await func(*args, **kwargs)

            # 2. Extract string
            if isinstance(raw_output, ProvenanceString):
                raw_text = raw_output.text
            elif hasattr(raw_output, "text"):
                raw_text = str(raw_output.text)
            else:
                raw_text = str(raw_output)
            raw_text = raw_text.strip()

            # 3. Enforce deterministic guardrail checks — MUST raise on failure
            if "verify_prohibited_sales_promises" in active_checks:
                verify_prohibited_sales_promises(raw_text, raise_on_failure=True)
            if "verify_building_code_citations" in active_checks:
                verify_building_code_citations(raw_text, raise_on_failure=True)

            # 4. Resolve source references (job ID, photo IDs, AST paths)
            source_refs: list[str] = list(kwargs.get("source_refs") or [])
            if not source_refs:
                job_id = kwargs.get("job_id")
                if not job_id and len(args) > 1:
                    for arg in args[1:]:
                        if hasattr(arg, "job_id") and getattr(arg, "job_id"):
                            job_id = str(getattr(arg, "job_id"))
                            break
                        if isinstance(arg, dict) and arg.get("id"):
                            job_id = str(arg.get("id"))
                            break
                if job_id:
                    source_refs.append(f"job:{job_id}")

            # 5. Resolve cryptographic prompt version hash
            p_hash = str(kwargs.get("prompt_version_hash") or "")
            if not p_hash:
                from app.services.ai.prompts import get_prompt_version_hash

                system_prompt = kwargs.get("system_prompt")
                if not system_prompt and len(args) > 1 and isinstance(args[1], str):
                    system_prompt = args[1]

                if system_prompt:
                    p_hash = get_prompt_version_hash(system_prompt)
                elif prompt_name:
                    p_hash = get_prompt_version_hash(prompt_name)
                else:
                    p_hash = get_prompt_version_hash("PROMPT_VERSION")

            model_name = getattr(args[0], "model_name", "gemini-3.5-flash") if args else "gemini-3.5-flash"

            logger.info(
                "ai_provenance_enforced",
                model=model_name,
                prompt_hash=p_hash[:8] if p_hash else "none",
                checks=active_checks,
                sources=source_refs,
            )

            return ProvenanceString(
                text=raw_text,
                source_refs=source_refs,
                guardrail_passed=True,
                guardrail_checks=active_checks,
                model_name=model_name,
                prompt_version_hash=p_hash,
            )

        return wrapper

    return decorator
