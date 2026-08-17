"""
Unit tests for the PDF Generator.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.core.inspection_models import (
    DamageType,
    InspectionJob,
    InspectionPhoto,
    PhotoAnalysis,
    Severity,
)
from app.core.supplement_models import MaterialBOM
from app.services.pdf import PDFGenerator


def test_generate_estimate_pdf_creates_file():
    """Test that the PDF generator creates a valid file on disk."""
    generator = PDFGenerator()

    test_data = {
        "materials": ["Asphalt Shingles", "Nails", "Underlayment"],
        "total_cost": 12500.50,
    }

    filepath = asyncio.run(generator.generate_estimate_pdf(test_data, "job123"))

    path_obj = Path(filepath)

    try:
        # Verify the file was created and is not empty
        assert path_obj.exists()
        assert path_obj.is_file()
        assert path_obj.stat().st_size > 0
    finally:
        # Clean up the temporary file
        if path_obj.exists():
            path_obj.unlink()


def test_generate_estimate_pdf_handles_missing_data():
    """Test PDF generation works even if data is missing."""
    generator = PDFGenerator()

    test_data = {}  # Empty data

    filepath = asyncio.run(generator.generate_estimate_pdf(test_data, "job456"))
    path_obj = Path(filepath)

    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()



def test_generate_material_po_creates_file():
    """Test that the Material PO generator creates a valid file."""
    generator = PDFGenerator()
    bom = MaterialBOM(
        field_shingle_bundles=30,
        starter_bundles=2,
        ridge_cap_bundles=3,
        ice_water_rolls=2,
        underlayment_rolls=5,
        drip_edge_pieces=15
    )
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    
    with patch("app.services.pdf.constants.FIELD_DOCS_DIR", Path(tempfile.gettempdir())):
        filepath = asyncio.run(generator.generate_material_po(job, bom, "ABC Supply", "2026-07-01"))
        path_obj = Path(filepath)
        try:
            assert path_obj.exists()
            assert path_obj.stat().st_size > 0
        finally:
            if path_obj.exists():
                path_obj.unlink()

def test_generate_notice_of_cancellation_creates_file():
    """Test that the Notice of Cancellation generator creates a valid file."""
    generator = PDFGenerator()
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    filepath = asyncio.run(generator.generate_notice_of_cancellation(job))
    path_obj = Path(filepath)
    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()

def test_generate_certificate_of_completion_creates_file():
    """Test that the Certificate of Completion generator creates a valid file."""
    generator = PDFGenerator()
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    filepath = asyncio.run(generator.generate_certificate_of_completion(job, "2026-07-10"))
    path_obj = Path(filepath)
    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()

def test_generate_contingency_agreement_creates_file():
    """Test that the Contingency Agreement generator creates a valid file."""
    generator = PDFGenerator()
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    filepath = asyncio.run(generator.generate_contingency_agreement(job))
    path_obj = Path(filepath)
    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()

def test_generate_evidence_grid_creates_file(tmp_path):
    """Test that the Evidence Grid creates a valid multi-page PDF."""
    generator = PDFGenerator()
    
    # Create a dummy image file for ReportLab to render
    from PIL import Image as PILImage
    img_path = tmp_path / "test_photo.jpg"
    img = PILImage.new("RGB", (800, 600), color="red")
    img.save(img_path, format="JPEG")
    
    sig_path = tmp_path / "sig.png"
    sig_img = PILImage.new("RGBA", (200, 50), color="blue")
    sig_img.save(sig_path, format="PNG")
    
    from datetime import datetime
    
    # Construct a valid InspectionJob
    job = InspectionJob(
        job_id="TEST-GRID-001",
        property_address="123 Test St",
        inspection_date=datetime.now(),
        photos=[
            InspectionPhoto(filepath=img_path, sha256="fake_hash", captured_at="2026-06-30T10:00:00Z")
        ],
        analyses=[
            PhotoAnalysis(
                filename=img_path.name,
                damage_detected=True,
                damage_type=DamageType.HAIL,
                severity=Severity.MODERATE,
                hail_hits_visible=True,
                crease_marks=False,
                granule_loss=True,
                exposed_fiberglass=False,
                confidence=0.98,
                forensic_narrative="Hail impacts visible across the soft metals and shingle mat."
            )
        ]
    )

    filepath = asyncio.run(generator.generate_evidence_grid(job, signature_path=str(sig_path)))
    path_obj = Path(filepath)

    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()

def test_generate_retail_contract_pdf_creates_file(tmp_path):
    """Test that the Retail Contract generator creates a valid file."""
    from app.services.pdf.documents import DocumentsGenerator
    generator = DocumentsGenerator()
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    
    # Create dummy signature
    from PIL import Image as PILImage
    sig_path = tmp_path / "sig.png"
    sig_img = PILImage.new("RGBA", (200, 50), color="blue")
    sig_img.save(sig_path, format="PNG")
    
    filepath = asyncio.run(generator.generate_retail_contract_pdf(
        job=job,
        signature_path=str(sig_path),
        signer_name="John Doe",
        ip_address="127.0.0.1",
        total_price_cents=1000000,
        deposit_cents=500000,
        scope_description="Replace roof with Architectural Shingles."
    ))
    path_obj = Path(filepath)
    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()

def test_generate_retail_notice_of_cancellation_creates_file():
    """Test that the Retail Notice of Cancellation generator creates a valid file."""
    from app.services.pdf.documents import DocumentsGenerator
    generator = DocumentsGenerator()
    job = {"id": "test_123", "homeowner_name": "Test", "address_line1": "123 St", "city": "City", "state": "GA", "postal_code": "30303", "claim_number": "123"}
    filepath = asyncio.run(generator.generate_retail_notice_of_cancellation(job))
    path_obj = Path(filepath)
    try:
        assert path_obj.exists()
        assert path_obj.stat().st_size > 0
    finally:
        if path_obj.exists():
            path_obj.unlink()
