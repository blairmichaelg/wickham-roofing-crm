from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.core.ingestion_models import ClaimLineItem, EvidenceRef, SourcedValue, UniversalClaimAST
from app.services.pdf_extractor import extract_eagleview_data
from app.services.supplement_engine import SupplementEngine


@pytest.fixture
def dummy_pdf_path(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"dummy pdf")
    return pdf

@pytest.fixture(autouse=True)
def mock_pdf_detector():
    with patch("app.core.pipeline.detect_pdf_format", return_value="EAGLEVIEW"), \
         patch("app.api.office_routes.detect_pdf_format", return_value="EAGLEVIEW"):
        yield

# 1. test_eagleview_raises_on_missing_pitch
@pytest.mark.asyncio
async def test_eagleview_raises_on_missing_pitch(dummy_pdf_path):
    # Mock text that has everything EXCEPT Predominant Pitch
    mock_text = """
    Total Roof Area = 3500.0 sq ft
    Ridges = 120 ft
    Valleys = 45 ft
    Eaves = 200 ft
    Rakes = 150 ft
    Hips = 50 ft
    """
    
    mock_page = MagicMock()
    mock_page.extract_text.return_value = mock_text
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    
    with patch("pdfplumber.open") as mock_open:
        mock_open.return_value.__enter__.return_value = mock_pdf
        with pytest.raises(ValueError, match="Predominant Pitch"):
            await extract_eagleview_data(dummy_pdf_path)

# 2. test_eagleview_raises_on_missing_hips
@pytest.mark.asyncio
async def test_eagleview_raises_on_missing_hips(dummy_pdf_path):
    # Mock text that has everything EXCEPT Hips
    mock_text = """
    Total Roof Area = 3500.0 sq ft
    Ridges = 120 ft
    Valleys = 45 ft
    Eaves = 200 ft
    Rakes = 150 ft
    Predominant Pitch = 6/12
    """
    
    mock_page = MagicMock()
    mock_page.extract_text.return_value = mock_text
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    
    with patch("pdfplumber.open") as mock_open:
        mock_open.return_value.__enter__.return_value = mock_pdf
        with pytest.raises(ValueError, match="Hip Length"):
            await extract_eagleview_data(dummy_pdf_path)

# 3. test_eagleview_returns_hash
@pytest.mark.asyncio
async def test_eagleview_returns_hash(dummy_pdf_path):
    mock_text = """
    Total Roof Area = 3500.0 sq ft
    Ridges = 120 ft
    Valleys = 45 ft
    Eaves = 200 ft
    Rakes = 150 ft
    Hips = 50 ft
    Predominant Pitch = 6/12
    """
    
    mock_page = MagicMock()
    mock_page.extract_text.return_value = mock_text
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    
    with patch("pdfplumber.open") as mock_open:
        mock_open.return_value.__enter__.return_value = mock_pdf
        ev_data, sha256_hash = await extract_eagleview_data(dummy_pdf_path)
        
        assert hasattr(ev_data, "hip_lf")
        assert ev_data.hip_lf == 50.0
        assert ev_data.predominant_pitch == "6/12"
        assert isinstance(sha256_hash, str)
        assert len(sha256_hash) == 64

# 4. test_esx_parser_raises_not_implemented
def test_esx_parser_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="ESX parsing is retired"):
        SupplementEngine.parse_esx("any_path.esx")

def create_mock_sourced_value(val: Decimal) -> SourcedValue[Decimal]:
    return SourcedValue(value=val, evidence=[EvidenceRef(doc_id="d", page=1, raw_text=str(val), extraction_method="test")])

def create_mock_sourced_str(val: str) -> SourcedValue[str]:
    return SourcedValue(value=val, evidence=[EvidenceRef(doc_id="d", page=1, raw_text=val, extraction_method="test")])

# 5. test_claim_line_item_flags_math_mismatch
def test_claim_line_item_flags_math_mismatch():
    # 10 * 5 + 2 = 52, but claimed_rcv = 60
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=create_mock_sourced_value(Decimal("10.00")),
        unit=create_mock_sourced_str("SQ"),
        unit_price=create_mock_sourced_value(Decimal("5.00")),
        tax=create_mock_sourced_value(Decimal("2.00")),
        claimed_rcv=create_mock_sourced_value(Decimal("60.00")), # Mismatch!
        depreciation=create_mock_sourced_value(Decimal("0.00")),
        acv=create_mock_sourced_value(Decimal("60.00")),
    )
    assert item.verified is False

# 6. test_claim_line_item_passes_valid_math
def test_claim_line_item_passes_valid_math():
    # 10 * 5 + 2 = 52
    item = ClaimLineItem(
        category_code="RFG",
        activity_code="300",
        description="Shingles",
        quantity=create_mock_sourced_value(Decimal("10.00")),
        unit=create_mock_sourced_str("SQ"),
        unit_price=create_mock_sourced_value(Decimal("5.00")),
        tax=create_mock_sourced_value(Decimal("2.00")),
        claimed_rcv=create_mock_sourced_value(Decimal("52.00")), # Match!
        depreciation=create_mock_sourced_value(Decimal("0.00")),
        acv=create_mock_sourced_value(Decimal("52.00")),
    )
    assert item.verified is True

# 7. test_universal_ast_requires_source_hash
def test_universal_ast_requires_source_hash():
    from pydantic import ValidationError

    from app.core.ingestion_models import ClaimFinancials, RoofGeometry
    
    geo = RoofGeometry(
        pitch=create_mock_sourced_str("6/12"),
        total_squares=create_mock_sourced_value(Decimal("10")),
        eaves_lf=create_mock_sourced_value(Decimal("100")),
        valleys_lf=create_mock_sourced_value(Decimal("50")),
        rakes_lf=create_mock_sourced_value(Decimal("100")),
    )
    fin = ClaimFinancials(
        gross_rcv=create_mock_sourced_value(Decimal("1000")),
        total_depreciation=create_mock_sourced_value(Decimal("0")),
        deductible=create_mock_sourced_value(Decimal("1000")),
        net_claim=create_mock_sourced_value(Decimal("0")),
    )
    
    with pytest.raises(ValidationError):
        # Missing source_doc_sha256 and source_doc_id
        UniversalClaimAST(
            line_items=[],
            roof_geometry=geo,
            financials=fin
        )

# 8. test_pipeline_halts_on_gross_rcv_mismatch
@pytest.mark.asyncio
async def test_pipeline_halts_on_gross_rcv_mismatch(tmp_path):
    import uuid

    from app.core.database import get_connection
    from app.core.pipeline import run_supplement_pipeline

    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute('''
            INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, "Test Homeowner", "123 St", "City", "ST", "12345", "555-5555", "LEAD_CAPTURED"))
        # Set manual flashing so it doesn't halt on Fix 1
        conn.execute("UPDATE jobs SET flashing_lf = 10, step_flashing_lf = 10 WHERE id = ?", (job_id,))
        # Insert the synthetic_math_rule to satisfy the foreign key constraint
        conn.execute('''
            INSERT OR IGNORE INTO supplement_rules (id, parent_code, required_child_code, citation_text, citation_type, trigger_logic_name, climate_dependent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', ("synthetic_math_rule", "SYS", "MATH", "Synthetic Math Rule", "INTERNAL_POLICY", "eval_math", 0))
        conn.commit()
    finally:
        conn.close()

    ev_pdf = tmp_path / "ev.pdf"
    sol_pdf = tmp_path / "sol.pdf"
    ev_pdf.write_bytes(b"dummy")
    sol_pdf.write_bytes(b"dummy")

    from app.core.supplement_models import EagleViewData
    mock_ev_data = EagleViewData(
        total_area_sf=1000.0, rake_lf=0.0, valley_lf=20.0, ridge_lf=0.0,
        hip_lf=0.0, eaves_lf=50.0, drip_edge_lf=0.0, flashing_lf=10.0,
        step_flashing_lf=10.0, total_facets=2, predominant_pitch="6/12"
    )

    from app.core.ingestion_models import ClaimFinancials, RoofGeometry
    mock_sol_data = UniversalClaimAST(
        line_items=[],
        roof_geometry=RoofGeometry(
            pitch=create_mock_sourced_str("6/12"),
            total_squares=create_mock_sourced_value(Decimal("10")),
            eaves_lf=create_mock_sourced_value(Decimal("100")),
            valleys_lf=create_mock_sourced_value(Decimal("50")),
            rakes_lf=create_mock_sourced_value(Decimal("100")),
        ),
        financials=ClaimFinancials(
            gross_rcv=create_mock_sourced_value(Decimal("1000")),
            total_depreciation=create_mock_sourced_value(Decimal("0")),
            deductible=create_mock_sourced_value(Decimal("1000")),
            net_claim=create_mock_sourced_value(Decimal("0")),
        ),
        source_doc_sha256="hash",
        source_doc_id="doc_id",
        ast_version=1
    )
    # Deliberately set verified to False
    mock_sol_data.financials.gross_rcv.verified = False

    with patch("app.core.pipeline.extract_eagleview_data", return_value=(mock_ev_data, "fake_hash")), \
         patch("app.services.document_parser.parse_statement_of_loss", return_value=mock_sol_data), \
         patch("app.services.ai_service.GeminiClient.generate_supplement_narrative") as mock_ai:
        
        result = await run_supplement_pipeline(
            job_id=job_id,
            ev_pdf_path=str(ev_pdf),
            sol_pdf_path=str(sol_pdf),
            ev_sha256="hash1",
            ev_doc_id="doc1",
            sol_sha256="hash2",
            sol_doc_id="doc2",
            resume=False
        )

        assert result["status"] == "halted_for_review"
        assert result["reason"] == "gross_rcv_mismatch"
        mock_ai.assert_not_called()
