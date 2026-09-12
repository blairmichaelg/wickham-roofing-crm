"""
Pydantic V2 models and validation parsers for Gemini AI structured responses.
"""

from typing import Any, Literal
from pydantic import BaseModel, Field, ValidationError

from app.core.inspection_models import PhotoAnalysis
from app.core.supplement_models import StatementOfLoss


class DocumentData(BaseModel):
    """Document data extracted or recommended during job analysis."""
    materials: list[str] = Field(default_factory=list)
    total_cost: float = 0.0


class Decision(BaseModel):
    """Structured decision output for CRM job intake/analysis."""
    action: Literal["generate_document", "update_status", "ignore", "error"]
    reasoning: str
    document_data: DocumentData = Field(default_factory=DocumentData)


class BatchPhotoAnalysis(BaseModel):
    """Container for batch analysis of roof photos."""
    analyses: list[PhotoAnalysis] = Field(default_factory=list)


def parse_job_decision(raw: str | dict[str, Any]) -> Decision:
    """Validate and parse raw Gemini JSON output into a Decision model."""
    if isinstance(raw, str):
        return Decision.model_validate_json(raw)
    return Decision.model_validate(raw)


def parse_batch_photo_analysis(raw: Any) -> list[PhotoAnalysis]:
    """Validate and parse batch photo analysis output."""
    if isinstance(raw, BatchPhotoAnalysis):
        return raw.analyses
    if isinstance(raw, str):
        batch = BatchPhotoAnalysis.model_validate_json(raw)
        return batch.analyses
    if isinstance(raw, dict):
        batch = BatchPhotoAnalysis.model_validate(raw)
        return batch.analyses
    if isinstance(raw, list):
        return [
            item if isinstance(item, PhotoAnalysis) else PhotoAnalysis.model_validate(item)
            for item in raw
        ]
    raise ValueError(f"Cannot parse batch photo analysis from type: {type(raw)}")


def parse_photo_analysis(raw: Any) -> PhotoAnalysis:
    """Validate and parse a single photo analysis output."""
    if isinstance(raw, PhotoAnalysis):
        return raw
    if isinstance(raw, str):
        return PhotoAnalysis.model_validate_json(raw)
    if isinstance(raw, dict):
        return PhotoAnalysis.model_validate(raw)
    raise ValueError(f"Cannot parse photo analysis from type: {type(raw)}")


def parse_statement_of_loss(raw: Any) -> StatementOfLoss:
    """Validate and parse a StatementOfLoss model."""
    if isinstance(raw, StatementOfLoss):
        return raw
    if isinstance(raw, str):
        return StatementOfLoss.model_validate_json(raw)
    if isinstance(raw, dict):
        return StatementOfLoss.model_validate(raw)
    raise ValueError(f"Cannot parse statement of loss from type: {type(raw)}")
