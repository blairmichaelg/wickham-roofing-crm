import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json

from app.services.document_parser import parse_statement_of_loss
from app.workers.inspection_processor import process_inspection
from app.core.inspection_models import InspectionJob, InspectionPhoto
from app.core.cache import init_db as init_cache_db

# Pytest fixture to initialize cache DB
@pytest.fixture(autouse=True)
def setup_cache():
    init_cache_db()

@pytest.mark.asyncio
async def test_parse_statement_of_loss(tmp_path):
    """
    Tests parse_statement_of_loss by mocking the Gemini AI client
    to return a simulated StatementOfLoss structure without hitting the API.
    """
    dummy_pdf = tmp_path / "dummy.pdf"
    dummy_pdf.touch()

    from app.core.supplement_models import StatementOfLoss, LineItem
    mock_sol = StatementOfLoss(
        carrier_name="State Farm",
        claim_number="1234",
        line_items=[
            LineItem(
                trade="RFG",
                code="300",
                description="Laminated - High Grade",
                quantity=30.0,
                unit_of_measure="SQ",
                unit_price=200.0,
                tax=10.0,
                claimed_rcv=6010.0,
                depreciation=0.0,
                acv=6010.0,
                page=1
            )
        ],
        pitch="6/12",
        total_squares=30.0,
        eaves_lf=100.0,
        valleys_lf=50.0,
        rakes_lf=50.0,
        gross_rcv=6010.0,
        total_depreciation=0.0,
        deductible=1000.0,
        net_claim=5010.0
    )

    with patch("app.services.document_parser.get_ai_client") as mock_get_client:
        mock_ai_client = MagicMock()
        
        async def mock_extract(*args, **kwargs):
            return mock_sol
            
        mock_ai_client.extract_sol_from_pdf = mock_extract
        mock_get_client.return_value = mock_ai_client
        
        ast = await parse_statement_of_loss(
            pdf_path=dummy_pdf,
            source_doc_sha256="fake_sha256",
            source_doc_id="fake_doc_id"
        )
        
        assert len(ast.line_items) == 1
        assert ast.line_items[0].activity_code == "300"
        assert ast.line_items[0].claimed_rcv.value == 6010.0
        assert ast.roof_geometry.total_squares.value == 30.0
        assert ast.financials.gross_rcv.value == 6010.0


@pytest.mark.asyncio
async def test_process_inspection(tmp_path, monkeypatch):
    """
    Tests process_inspection by mocking get_ai_client() to simulate 
    PhotoAnalysis parsing without making network calls.
    """
    job_id = "test-job-123"
    
    # Mock FIELD_PHOTOS_DIR and SIGNED_AGREEMENTS_DIR
    test_field_photos = tmp_path / "field_photos"
    test_field_docs = tmp_path / "field_docs"
    test_field_photos.mkdir()
    test_field_docs.mkdir()
    
    # Create a job dir
    job_dir = test_field_photos / job_id
    job_dir.mkdir()
    
    photo_file = job_dir / "test_roof.jpg"
    from PIL import Image
    img = Image.new('RGB', (100, 100), color='red')
    img.save(photo_file)
    
    monkeypatch.setattr("app.workers.inspection_processor.FIELD_DOCS_DIR", test_field_docs)

    from app.core.inspection_models import PhotoAnalysis, DamageType, Severity

    from datetime import datetime
    
    mock_job = InspectionJob(
        job_id=job_id,
        property_address="123 Test St",
        inspection_date=datetime.now(),
        photos=[
            InspectionPhoto(
                filepath=photo_file,
                sha256="fake_hash_abc",
                captured_at=None
            )
        ],
        analyses=[]
    )

    with patch("app.workers.inspection_processor.get_inspection_summary", return_value=mock_job), \
         patch("app.workers.inspection_processor.get_ai_client") as mock_get_client:
        
        mock_ai_client = MagicMock()
        
        async def mock_upload(*args, **kwargs):
            return "remote_fake_file"
            
        async def mock_status(*args, **kwargs):
            return "ACTIVE"
            
        async def mock_analyze(*args, **kwargs):
            return PhotoAnalysis(
                filename="test_roof.jpg",
                damage_detected=True,
                damage_type=DamageType.HAIL,
                severity=Severity.SEVERE,
                confidence=0.95,
                forensic_narrative="Simulated hail damage."
            )
            
        async def mock_delete(*args, **kwargs):
            pass
            
        async def mock_batch_analyze(*args, **kwargs):
            return [
                PhotoAnalysis(
                    filename="test_roof.jpg",
                    damage_detected=True,
                    damage_type=DamageType.HAIL,
                    severity=Severity.SEVERE,
                    confidence=0.95,
                    forensic_narrative="Simulated hail damage."
                )
            ]
            
        mock_ai_client.upload_media_file = mock_upload
        mock_ai_client.get_file_status = mock_status
        mock_ai_client.analyze_roof_photo = mock_analyze
        mock_ai_client.analyze_roof_photos_batch = mock_batch_analyze
        mock_ai_client.delete_file = mock_delete
        
        mock_get_client.return_value = mock_ai_client
        
        ctx = {"is_test": True}
        processed_job = await process_inspection(ctx, job_id)
        
        assert len(processed_job.analyses) == 1
        analysis = processed_job.analyses[0]
        assert analysis.damage_detected is True
        assert analysis.severity == Severity.SEVERE
        assert analysis.forensic_narrative == "Simulated hail damage."
