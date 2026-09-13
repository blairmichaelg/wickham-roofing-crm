"""
tests/test_esx_parser_and_security.py — Tests for Phase D ESX Parser & Security Defense.

Verifies:
1. Valid synthetic ESX archive parsing into UniversalClaimAST with precise decimals.
2. Security defense: Member count limit (>50 entries).
3. Security defense: Path traversal rejection.
4. Security defense: XML XXE / DOCTYPE entity expansion attack prevention.
5. Bad zip rejection.
6. Settings feature flag (enable_esx_import=False default).
"""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from pathlib import Path
import pytest

from app.config import Settings
from app.services.esx_parser import (
    ESXSecurityError,
    ESXParseError,
    validate_and_extract_xml,
    parse_safe_xml,
    parse_esx_to_ast,
)


def _create_synthetic_esx(xml_content: str, filename: str = "estimate.xml", extra_files: dict[str, bytes] | None = None) -> bytes:
    """Helper to create a synthetic in-memory ESX zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, xml_content.encode("utf-8"))
        if extra_files:
            for fname, data in extra_files.items():
                zf.writestr(fname, data)
    return buf.getvalue()


VALID_ESTIMATE_XML = """<?xml version="1.0" encoding="utf-8"?>
<XactimateEstimate>
    <ProjectInfo>
        <CLAIM_NUMBER>CLM-2026-8812</CLAIM_NUMBER>
        <INSURER_NAME>State Farm</INSURER_NAME>
    </ProjectInfo>
    <LineItems>
        <LINE_ITEM>
            <CAT>RFG</CAT>
            <ACT>&amp;</ACT>
            <DESC>Tear off architectural shingles</DESC>
            <QTY>32.0</QTY>
            <UNIT>SQ</UNIT>
            <UNIT_PRICE>65.50</UNIT_PRICE>
            <RCV>2096.00</RCV>
            <DEPRECIATION>0.00</DEPRECIATION>
            <ACV>2096.00</ACV>
        </LINE_ITEM>
        <LINE_ITEM>
            <CAT>RFG</CAT>
            <ACT>+</ACT>
            <DESC>Install 30-yr laminate shingles</DESC>
            <QTY>34.5</QTY>
            <UNIT>SQ</UNIT>
            <UNIT_PRICE>185.00</UNIT_PRICE>
            <RCV>6382.50</RCV>
            <DEPRECIATION>1200.00</DEPRECIATION>
            <ACV>5182.50</ACV>
        </LINE_ITEM>
    </LineItems>
    <Summary>
        <TOTAL_RCV>8478.50</TOTAL_RCV>
        <TOTAL_DEPRECIATION>1200.00</TOTAL_DEPRECIATION>
        <DEDUCTIBLE>1000.00</DEDUCTIBLE>
        <NET_CLAIM>6278.50</NET_CLAIM>
    </Summary>
</XactimateEstimate>
"""


def test_valid_synthetic_esx_parsing():
    """Verify parsing a valid ESX archive extracts metadata, line items, and financial values."""
    esx_bytes = _create_synthetic_esx(VALID_ESTIMATE_XML)
    ast = parse_esx_to_ast(esx_bytes, source_doc_id="doc_test_1")

    assert ast.claim_number.value == "CLM-2026-8812"
    assert ast.insurer_name.value == "State Farm"
    assert len(ast.line_items) == 2

    item1 = ast.line_items[0]
    assert item1.category_code == "RFG"
    assert item1.claimed_rcv.value == Decimal("2096.00")
    assert item1.acv.value == Decimal("2096.00")

    item2 = ast.line_items[1]
    assert item2.category_code == "RFG"
    assert item2.claimed_rcv.value == Decimal("6382.50")
    assert item2.depreciation.value == Decimal("1200.00")
    assert item2.acv.value == Decimal("5182.50")

    # Financials
    assert ast.financials.gross_rcv.value == Decimal("8478.50")
    assert ast.financials.total_depreciation.value == Decimal("1200.00")
    assert ast.financials.deductible.value == Decimal("1000.00")
    assert ast.financials.net_claim.value == Decimal("6278.50")


def test_esx_security_rejects_xxe_entity_expansion():
    """Verify that any XML entity definition (XXE / billion laughs) is rejected."""
    xxe_xml = """<?xml version="1.0"?>
    <!DOCTYPE foo [
      <!ELEMENT foo ANY >
      <!ENTITY xxe SYSTEM "file:///etc/passwd" >]>
    <XactimateEstimate>
        <ProjectInfo>
            <CLAIM_NUMBER>&xxe;</CLAIM_NUMBER>
        </ProjectInfo>
    </XactimateEstimate>
    """
    esx_bytes = _create_synthetic_esx(xxe_xml)
    with pytest.raises(ESXSecurityError, match="prohibited DOCTYPE or ENTITY"):
        parse_esx_to_ast(esx_bytes)


def test_esx_security_rejects_doctype():
    """Verify DOCTYPE declarations are rejected by parse_safe_xml."""
    doctype_bytes = b'<?xml version="1.0"?><!DOCTYPE test SYSTEM "test.dtd"><root/>'
    with pytest.raises(ESXSecurityError, match="prohibited DOCTYPE or ENTITY"):
        parse_safe_xml(doctype_bytes)


def test_esx_security_rejects_member_count_bomb():
    """Verify archives containing >50 files are rejected as potential resource exhaustion."""
    extra = {f"file_{i}.txt": b"test content" for i in range(55)}
    esx_bytes = _create_synthetic_esx(VALID_ESTIMATE_XML, extra_files=extra)
    with pytest.raises(ESXSecurityError, match="exceeds maximum allowed"):
        validate_and_extract_xml(esx_bytes)


def test_esx_security_rejects_path_traversal():
    """Verify entries attempting directory traversal (e.g. ../../target) are blocked."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../root_file.xml", VALID_ESTIMATE_XML.encode("utf-8"))
    esx_bytes = buf.getvalue()

    with pytest.raises(ESXSecurityError, match="Path traversal detected"):
        validate_and_extract_xml(esx_bytes)


def test_esx_bad_zip_rejection():
    """Verify corrupted zip archive raises ESXParseError."""
    with pytest.raises(ESXParseError, match="not a valid ZIP"):
        validate_and_extract_xml(b"not a zip file at all")


def test_esx_feature_flag_default():
    """Verify enable_esx_import flag defaults to False in application settings."""
    settings = Settings()
    assert settings.enable_esx_import is False


def test_realistic_contractor_8d_fixture():
    """Verify end-to-end parsing of a realistic multi-line-item 8D contractor fixture."""
    fixture_path = Path("tests/fixtures/esx/realistic_contractor_8d.esx")
    assert fixture_path.exists(), "Realistic 8D fixture must exist"
    file_bytes = fixture_path.read_bytes()

    ast = parse_esx_to_ast(file_bytes, source_doc_id="doc_8d_test")

    # Verify claim identity & metadata
    assert ast.claim_number.value == "ESX-SYNTH-8D-2026"
    assert "Georgia Farm" in ast.insurer_name.value
    assert "Profile: 8D" in ast.financials.gross_rcv.evidence[0].raw_text

    # Verify roof geometry
    assert ast.roof_geometry.total_squares.value == Decimal("32.00")
    assert ast.roof_geometry.eaves_lf.value == Decimal("140.00")
    assert ast.roof_geometry.valleys_lf.value == Decimal("35.00")
    assert ast.roof_geometry.rakes_lf.value == Decimal("85.00")

    # Verify line items extraction
    assert len(ast.line_items) == 4
    tear_off = ast.line_items[0]
    assert tear_off.category_code == "RFG"
    assert "Tear off comp shingles" in tear_off.description
    assert tear_off.quantity.value == Decimal("32.00")
    assert tear_off.unit_price.value == Decimal("55.00")
    assert tear_off.claimed_rcv.value == Decimal("1760.00")
    assert tear_off.depreciation.value == Decimal("0.00")
    assert tear_off.acv.value == Decimal("1760.00")

    shingle = ast.line_items[1]
    assert "Laminated shingle" in shingle.description
    assert shingle.quantity.value == Decimal("35.00")
    assert shingle.unit_price.value == Decimal("225.00")
    assert shingle.claimed_rcv.value == Decimal("7875.00")
    assert shingle.depreciation.value == Decimal("787.50")
    assert shingle.acv.value == Decimal("7087.50")

    # Financials reconciliation to the penny
    assert ast.financials.gross_rcv.value == Decimal("10596.00")
    assert ast.financials.total_depreciation.value == Decimal("883.60")
    assert ast.financials.deductible.value == Decimal("1000.00")
    assert ast.financials.net_claim.value == Decimal("8712.40")

    # Header matches line items sum exactly
    assert ast.financials.gross_rcv.verified is True
    assert ast.financials.net_claim.verified is True


def test_realistic_carrier_5l_fixture():
    """Verify parsing of realistic carrier 5L profile fixture."""
    fixture_path = Path("tests/fixtures/esx/realistic_carrier_5l.esx")
    assert fixture_path.exists(), "Realistic 5L fixture must exist"
    file_bytes = fixture_path.read_bytes()

    ast = parse_esx_to_ast(file_bytes, source_doc_id="doc_5l_test")

    assert ast.claim_number.value == "ESX-SYNTH-5L-2026"
    assert "Profile: 5L" in ast.financials.gross_rcv.evidence[0].raw_text
    assert len(ast.line_items) == 4
    assert ast.financials.gross_rcv.value == Decimal("10596.00")
    assert ast.financials.gross_rcv.verified is True


def test_mismatched_totals_fixture_flags_discrepancy():
    """Verify that when header gross RCV does not match line item sum, gross_rcv.verified is flagged False."""
    fixture_path = Path("tests/fixtures/esx/mismatched_totals.esx")
    assert fixture_path.exists(), "Mismatched fixture must exist"
    file_bytes = fixture_path.read_bytes()

    ast = parse_esx_to_ast(file_bytes, source_doc_id="doc_mismatch_test")

    # Line items sum to $10,596.00, but header claimed $14,000.00
    assert ast.financials.gross_rcv.value == Decimal("14000.00")
    # Discrepancy correctly flagged as not verified
    assert ast.financials.gross_rcv.verified is False
    # Overall equation gross - dep - ded == net still matches header numbers
    assert ast.financials.net_claim.verified is True


def test_esx_parser_edge_cases():
    """Verify edge cases: sanitize decimal invalid format, no line items, compressed size limit."""
    from app.services.esx_parser import _sanitize_decimal, MAX_COMPRESSED_BYTES

    # Invalid decimal raises ESXParseError
    with pytest.raises(ESXParseError, match="Invalid monetary"):
        _sanitize_decimal("not-a-number")

    # Empty XML / no line items raises ESXParseError
    empty_xml = "<XactimateEstimate><PROJECT_INFO><CLAIM_NUMBER>123</CLAIM_NUMBER></PROJECT_INFO></XactimateEstimate>"
    empty_esx = _create_synthetic_esx(empty_xml)
    with pytest.raises(ESXParseError, match="no recognizable line items"):
        parse_esx_to_ast(empty_esx)

    # Compressed size limit raises ESXSecurityError
    with pytest.raises(ESXSecurityError, match="compressed size"):
        validate_and_extract_xml(b"0" * (MAX_COMPRESSED_BYTES + 10))

