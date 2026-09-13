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
