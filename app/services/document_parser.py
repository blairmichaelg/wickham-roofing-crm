"""
Gemini File API Statement of Loss Parser.

Architecture:
  - Gemini Multimodal File API for structured StatementOfLoss extraction.
  - Pydantic models for type accuracy.
  - Python re-verifies carrier arithmetic downstream in reconciliation.py.

The LLM is a LOCATOR, not a CALCULATOR.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from app.core.ingestion_models import (
    ClaimFinancials,
    ClaimLineItem,
    EvidenceRef,
    RoofGeometry,
    SourcedValue,
    UniversalClaimAST,
)
from app.services.ai_service import get_ai_client

logger = structlog.get_logger("app.services.document_parser")


async def parse_statement_of_loss(
    pdf_path: Path,
    source_doc_sha256: str,
    source_doc_id: str,
) -> UniversalClaimAST:
    """
    Full multimodal SoL parse using Gemini File API.

    Extracts all claim details, line items, geometry, and financials,
    and returns a UniversalClaimAST with every value sourced.

    Args:
        pdf_path: Path to the Statement of Loss PDF file.
        source_doc_sha256: SHA256 checksum of the source document.
        source_doc_id: FK database ID of the document.

    Returns:
        UniversalClaimAST: The constructed and mathematically verified AST.

    Raises:
        ValueError: If the PDF is unreadable, Gemini fails, or verification fails.
    """
    log = logger.bind(pdf_path=str(pdf_path), sha256=source_doc_sha256)
    log.info("sol_parse_started")

    ai_client = get_ai_client()

    try:
        # Perform structured extraction using Gemini File API (encapsulated in ai_client)
        parsed = await ai_client.extract_sol_from_pdf(str(pdf_path), job_id=source_doc_id)
    except Exception as exc:
        log.error("sol_parse_gemini_failed", error=str(exc))
        raise ValueError(f"Gemini Statement of Loss extraction failed: {exc}") from exc

    def _make_evidence(page: int, raw: str) -> EvidenceRef:
        """Helper to create EvidenceRef for a value."""
        return EvidenceRef(
            doc_id=source_doc_id,
            page=page,
            raw_text=raw[:200] if raw else "",
            extraction_method="gemini-2.5-flash-multimodal"
        )

    def _sourced(value: Any, page: int, raw: str) -> SourcedValue[Any]:
        """Helper to create a SourcedValue wrapper."""
        return SourcedValue(
            value=value,
            evidence=[_make_evidence(page, raw)]
        )

    # Build line items
    line_items = []
    for item in parsed.line_items:
        page = item.page or 1
        try:
            li = ClaimLineItem(
                category_code=item.trade or "UNKNOWN",
                activity_code=item.code or "UNKNOWN",
                description=item.description or "",
                quantity=_sourced(
                    Decimal(str(item.quantity)) if item.quantity is not None else Decimal(0),
                    page, str(item.quantity)
                ),
                unit=_sourced(item.unit_of_measure or "EA", page, str(item.unit_of_measure)),
                unit_price=_sourced(
                    Decimal(str(item.unit_price)) if item.unit_price is not None else Decimal(0),
                    page, str(item.unit_price)
                ),
                tax=_sourced(
                    Decimal(str(item.tax or "0")),
                    page, str(item.tax)
                ),
                claimed_rcv=_sourced(
                    Decimal(str(item.claimed_rcv)) if item.claimed_rcv is not None else Decimal(0),
                    page, str(item.claimed_rcv)
                ),
                depreciation=_sourced(
                    Decimal(str(item.depreciation or "0")),
                    page, str(item.depreciation)
                ),
                acv=_sourced(
                    Decimal(str(item.acv or "0")),
                    page, str(item.acv)
                ),
            )
            line_items.append(li)
        except Exception as e:
            logger.warning("sol_line_item_skipped", error=str(e), item=item)
            continue

    # Extract geometry and financials
    geometry = RoofGeometry(
        pitch=_sourced(str(parsed.pitch or "unknown"), 1, str(parsed.pitch)),
        total_squares=_sourced(Decimal(str(parsed.total_squares or "0")), 1, str(parsed.total_squares)),
        eaves_lf=_sourced(Decimal(str(parsed.eaves_lf or "0")), 1, str(parsed.eaves_lf)),
        valleys_lf=_sourced(Decimal(str(parsed.valleys_lf or "0")), 1, str(parsed.valleys_lf)),
        rakes_lf=_sourced(Decimal(str(parsed.rakes_lf or "0")), 1, str(parsed.rakes_lf)),
    )

    financials = ClaimFinancials(
        gross_rcv=_sourced(Decimal(str(parsed.gross_rcv or "0")), 1, str(parsed.gross_rcv)),
        total_depreciation=_sourced(Decimal(str(parsed.total_depreciation or "0")), 1, str(parsed.total_depreciation)),
        deductible=_sourced(Decimal(str(parsed.deductible or "0")), 1, str(parsed.deductible)),
        net_claim=_sourced(Decimal(str(parsed.net_claim or "0")), 1, str(parsed.net_claim)),
    )

    ast = UniversalClaimAST(
        line_items=line_items,
        roof_geometry=geometry,
        financials=financials,
        claim_number=_sourced(str(parsed.claim_number), 1, str(parsed.claim_number)) if parsed.claim_number else None,
        insurer_name=_sourced(str(parsed.carrier_name), 1, str(parsed.carrier_name)) if parsed.carrier_name else None,
        shingle_type=parsed.shingle_type if parsed.shingle_type else None,
        shingle_color=parsed.shingle_color if parsed.shingle_color else None,
        source_doc_sha256=source_doc_sha256,
        source_doc_id=source_doc_id,
        ast_version=1,
    )

    unverified = [li for li in ast.line_items if not li.verified]
    log.info(
        "sol_parse_complete",
        total_items=len(ast.line_items),
        unverified_count=len(unverified),
        sha256=source_doc_sha256,
    )

    return ast
