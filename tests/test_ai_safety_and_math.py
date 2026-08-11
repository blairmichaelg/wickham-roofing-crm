"""
Unit tests for AI safety, math verification, and schema constraints.
"""

from decimal import Decimal
from pathlib import Path
import pytest
from pydantic import ValidationError

from app.core.ingestion_models import (
    UniversalClaimAST,
    ClaimLineItem,
    RoofGeometry,
    ClaimFinancials,
    SourcedValue,
    EvidenceRef,
)
from app.core.inspection_models import PhotoAnalysis, DamageType, Severity


def test_prompt_templates_for_critical_directives():
    """
    Statically inspects the ai_service.py file to ensure all prompt templates
    include strict 'no-math' and 'no-calculation' directives.
    """
    ai_service_path = Path("app/services/ai_service.py")
    assert ai_service_path.exists()
    content = ai_service_path.read_text(encoding="utf-8")

    # The prompt should contain critical keywords preventing mathematical calculation
    assert "CRITICAL NO-MATH DIRECTIVE" in content
    assert "DO NOT perform any arithmetic calculations" in content
    assert "The AI must NEVER calculate" in content


def _make_sourced(val, doc_id="doc1", page=1):
    return SourcedValue(
        value=val,
        evidence=[
            EvidenceRef(
                doc_id=doc_id,
                page=page,
                raw_text=str(val),
                extraction_method="test",
            )
        ],
    )


def test_ast_math_validation_valid():
    """
    A mathematically consistent UniversalClaimAST should pass validation.
    """
    # 20.0 SQ * 300.0 UP = 6000.0 + 10.0 tax = 6010.0 RCV
    # 6010.0 RCV - 10.0 dep = 6000.0 ACV
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=_make_sourced(Decimal("20.00")),
        unit=_make_sourced("SQ"),
        unit_price=_make_sourced(Decimal("300.00")),
        tax=_make_sourced(Decimal("10.00")),
        claimed_rcv=_make_sourced(Decimal("6010.00")),
        depreciation=_make_sourced(Decimal("10.00")),
        acv=_make_sourced(Decimal("6000.00")),
    )

    geom = RoofGeometry(
        pitch=_make_sourced("6/12"),
        total_squares=_make_sourced(Decimal("20.00")),
        eaves_lf=_make_sourced(Decimal("100.00")),
        valleys_lf=_make_sourced(Decimal("50.00")),
        rakes_lf=_make_sourced(Decimal("50.00")),
    )

    # gross_rcv (6010) - total_depreciation (10) - deductible (1000) = net_claim (5000)
    financials = ClaimFinancials(
        gross_rcv=_make_sourced(Decimal("6010.00")),
        total_depreciation=_make_sourced(Decimal("10.00")),
        deductible=_make_sourced(Decimal("1000.00")),
        net_claim=_make_sourced(Decimal("5000.00")),
    )

    ast = UniversalClaimAST(
        line_items=[item],
        roof_geometry=geom,
        financials=financials,
        source_doc_sha256="fake_sha",
        source_doc_id="doc1",
    )
    assert ast.financials.gross_rcv.verified is True
    assert ast.financials.net_claim.verified is True
    assert ast.line_items[0].verified is True


def test_ast_math_validation_financials_mismatch():
    """
    UniversalClaimAST should raise ValueError if overall claim financials mismatch.
    """
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=_make_sourced(Decimal("20.00")),
        unit=_make_sourced("SQ"),
        unit_price=_make_sourced(Decimal("300.00")),
        tax=_make_sourced(Decimal("10.00")),
        claimed_rcv=_make_sourced(Decimal("6010.00")),
        depreciation=_make_sourced(Decimal("10.00")),
        acv=_make_sourced(Decimal("6000.00")),
    )

    geom = RoofGeometry(
        pitch=_make_sourced("6/12"),
        total_squares=_make_sourced(Decimal("20.00")),
        eaves_lf=_make_sourced(Decimal("100.00")),
        valleys_lf=_make_sourced(Decimal("50.00")),
        rakes_lf=_make_sourced(Decimal("50.00")),
    )

    # gross_rcv (6010) - total_depreciation (10) - deductible (1000) = expected net (5000)
    # but net_claim is passed as 4500 (mismatch)
    financials = ClaimFinancials(
        gross_rcv=_make_sourced(Decimal("6010.00")),
        total_depreciation=_make_sourced(Decimal("10.00")),
        deductible=_make_sourced(Decimal("1000.00")),
        net_claim=_make_sourced(Decimal("4500.00")),
    )

    with pytest.raises(ValueError, match="Overall claim financials mismatch"):
        UniversalClaimAST(
            line_items=[item],
            roof_geometry=geom,
            financials=financials,
            source_doc_sha256="fake_sha",
            source_doc_id="doc1",
        )


def test_ast_math_validation_line_item_mismatch():
    """
    UniversalClaimAST should raise ValueError if any line item's RCV/Depreciation/ACV math mismatches.
    """
    # claimed_rcv (6010) - depreciation (10) = expected acv (6000)
    # but acv is passed as 5500 (mismatch)
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=_make_sourced(Decimal("20.00")),
        unit=_make_sourced("SQ"),
        unit_price=_make_sourced(Decimal("300.00")),
        tax=_make_sourced(Decimal("10.00")),
        claimed_rcv=_make_sourced(Decimal("6010.00")),
        depreciation=_make_sourced(Decimal("10.00")),
        acv=_make_sourced(Decimal("5500.00")),
    )

    geom = RoofGeometry(
        pitch=_make_sourced("6/12"),
        total_squares=_make_sourced(Decimal("20.00")),
        eaves_lf=_make_sourced(Decimal("100.00")),
        valleys_lf=_make_sourced(Decimal("50.00")),
        rakes_lf=_make_sourced(Decimal("50.00")),
    )

    financials = ClaimFinancials(
        gross_rcv=_make_sourced(Decimal("6010.00")),
        total_depreciation=_make_sourced(Decimal("10.00")),
        deductible=_make_sourced(Decimal("1000.00")),
        net_claim=_make_sourced(Decimal("5000.00")),
    )

    with pytest.raises(ValueError, match="Line item 'Shingles' arithmetic mismatch"):
        UniversalClaimAST(
            line_items=[item],
            roof_geometry=geom,
            financials=financials,
            source_doc_sha256="fake_sha",
            source_doc_id="doc1",
        )


def test_ast_math_validation_negative_geometry():
    """
    UniversalClaimAST should raise ValueError if any geometry values are negative.
    """
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=_make_sourced(Decimal("20.00")),
        unit=_make_sourced("SQ"),
        unit_price=_make_sourced(Decimal("300.00")),
        tax=_make_sourced(Decimal("10.00")),
        claimed_rcv=_make_sourced(Decimal("6010.00")),
        depreciation=_make_sourced(Decimal("10.00")),
        acv=_make_sourced(Decimal("6000.00")),
    )

    # negative total_squares (-5.00)
    geom = RoofGeometry(
        pitch=_make_sourced("6/12"),
        total_squares=_make_sourced(Decimal("-5.00")),
        eaves_lf=_make_sourced(Decimal("100.00")),
        valleys_lf=_make_sourced(Decimal("50.00")),
        rakes_lf=_make_sourced(Decimal("50.00")),
    )

    financials = ClaimFinancials(
        gross_rcv=_make_sourced(Decimal("6010.00")),
        total_depreciation=_make_sourced(Decimal("10.00")),
        deductible=_make_sourced(Decimal("1000.00")),
        net_claim=_make_sourced(Decimal("5000.00")),
    )

    with pytest.raises(ValueError, match="Roof geometry field 'total_squares' cannot be negative"):
        UniversalClaimAST(
            line_items=[item],
            roof_geometry=geom,
            financials=financials,
            source_doc_sha256="fake_sha",
            source_doc_id="doc1",
        )


def test_photo_analysis_validation_bounds():
    """
    PhotoAnalysis confidence and confidence_score must satisfy bounds [0, 1] and [0, 100].
    """
    # Valid
    pa = PhotoAnalysis(
        filename="test.jpg",
        damage_detected=True,
        damage_type=DamageType.HAIL,
        severity=Severity.MINOR,
        confidence=0.8,
        confidence_score=80.0,
        forensic_narrative="Hail damage seen on shingle slope.",
    )
    assert pa.confidence == 0.8
    assert pa.confidence_score == 80.0

    # Invalid confidence (> 1.0)
    with pytest.raises(ValidationError):
        PhotoAnalysis(
            filename="test.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.MINOR,
            confidence=1.5,
            confidence_score=80.0,
            forensic_narrative="Hail damage seen on shingle slope.",
        )

    # Invalid confidence_score (> 100.0)
    with pytest.raises(ValidationError):
        PhotoAnalysis(
            filename="test.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.MINOR,
            confidence=0.8,
            confidence_score=150.0,
            forensic_narrative="Hail damage seen on shingle slope.",
        )
