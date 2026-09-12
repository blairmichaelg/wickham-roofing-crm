"""
Guarded AI pipeline for Wickham Roofing CRM.

Modular architecture:
- client: Low-level Gemini API transport, authentication, retry/backoff.
- prompts: Static, versioned registry of system prompts.
- parsers: Pydantic V2 response parsers and validation schemas.
- guardrails: Deterministic, non-AI safety and arithmetic validation.
"""

from app.services.ai.client import GeminiTransportClient
from app.services.ai.guardrails import (
    GuardrailResult,
    GuardrailValidationError,
    check_all_guardrails,
    verify_legal_disclaimers,
    verify_math_reconciliation,
)
from app.services.ai.parsers import (
    BatchPhotoAnalysis,
    Decision,
    DocumentData,
    parse_batch_photo_analysis,
    parse_job_decision,
    parse_photo_analysis,
    parse_statement_of_loss,
)
from app.services.ai.prompts import (
    BATCH_ROOF_PHOTO_INSPECTION_PROMPT,
    CLASSIFY_CARRIER_PROMPT,
    DOOR_SCRIPT_SYSTEM_PROMPT,
    JOB_DATA_ANALYSIS_PROMPT_TEMPLATE,
    PROMPT_VERSION,
    ROOF_PHOTO_INSPECTION_TEMPLATE,
    SALES_SUMMARY_SYSTEM_PROMPT,
    SOL_GENERIC_PROMPT,
    SOL_SYMBILITY_PROMPT,
    SOL_XACTIMATE_PROMPT,
    SUPPLEMENT_NARRATIVE_TEMPLATE,
)


def get_ai_client():
    from app.services.ai_service import get_ai_client as _get
    return _get()


def __getattr__(name: str):
    if name in ("AiClient", "GeminiClient"):
        from app.services.ai_service import AiClient, GeminiClient
        return {"AiClient": AiClient, "GeminiClient": GeminiClient}[name]
    raise AttributeError(f"module 'app.services.ai' has no attribute '{name}'")


__all__ = [
    "GeminiTransportClient",
    "AiClient",
    "GeminiClient",
    "get_ai_client",
    "GuardrailResult",
    "GuardrailValidationError",
    "check_all_guardrails",
    "verify_legal_disclaimers",
    "verify_math_reconciliation",
    "BatchPhotoAnalysis",
    "Decision",
    "DocumentData",
    "parse_batch_photo_analysis",
    "parse_job_decision",
    "parse_photo_analysis",
    "parse_statement_of_loss",
    "PROMPT_VERSION",
    "JOB_DATA_ANALYSIS_PROMPT_TEMPLATE",
    "CLASSIFY_CARRIER_PROMPT",
    "SOL_XACTIMATE_PROMPT",
    "SOL_SYMBILITY_PROMPT",
    "SOL_GENERIC_PROMPT",
    "SUPPLEMENT_NARRATIVE_TEMPLATE",
    "ROOF_PHOTO_INSPECTION_TEMPLATE",
    "BATCH_ROOF_PHOTO_INSPECTION_PROMPT",
    "SALES_SUMMARY_SYSTEM_PROMPT",
    "DOOR_SCRIPT_SYSTEM_PROMPT",
]
