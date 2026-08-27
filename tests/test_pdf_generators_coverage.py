import asyncio
import os
import pytest
import uuid
import tempfile
from pathlib import Path
from PIL import Image as PILImage
from datetime import datetime

from app.core.inspection_models import (
    DamageType,
    InspectionJob,
    InspectionPhoto,
    PhotoAnalysis,
    Severity,
)
from app.core.supplement_models import (
    MaterialBOM,
    DiscrepancyReport,
    Discrepancy
)
from app.services.pdf.inspection_report import InspectionReportGenerator
from app.services.pdf.supplement import SupplementGenerator
from app.services.pdf.invoice import InvoiceGenerator
from app.core.database import get_connection

@pytest.fixture
def test_resources(tmp_path):
    """Fixture to provide temp paths and generated images/signatures."""
    # Create temp directories
    test_docs_dir = tmp_path / "field_docs"
    test_docs_dir.mkdir()

    # Create temp image and signature files
    img_path = tmp_path / "test_photo.jpg"
    img = PILImage.new("RGB", (800, 600), color="red")
    img.save(img_path, format="JPEG")
    
    sig_path = tmp_path / "sig.png"
    sig_img = PILImage.new("RGBA", (200, 50), color="blue")
    sig_img.save(sig_path, format="PNG")

    yield {
        "docs_dir": test_docs_dir,
        "img_path": img_path,
        "sig_path": sig_path,
    }

@pytest.fixture
def db_job():
    """Create a temporary job record in SQLite to satisfy DB reads during report generation."""
    conn = get_connection()
    job_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, job_type, status, inspector_name, canvasser_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, "Jane Homeowner", "789 Pine Rd", "Valdosta", "GA", "31602", "555-0100", "INSURANCE", "LEAD_CAPTURED", "Michael Inspector", "Scott Canvasser")
    )
    conn.commit()
    conn.close()
    yield job_id

    # Cleanup
    conn = get_connection()
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.execute("DELETE FROM job_documents WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()

def test_generate_homeowner_report_coverage(test_resources, db_job, monkeypatch):
    """Test InspectionReportGenerator homeowner report path."""
    job_id = db_job
    img_path = test_resources["img_path"]
    sig_path = test_resources["sig_path"]
    monkeypatch.setattr("app.services.pdf.inspection_report.FIELD_DOCS_DIR", test_resources["docs_dir"])

    # Construct complete InspectionJob
    job = InspectionJob(
        job_id=job_id,
        property_address="789 Pine Rd, Valdosta, GA 31602",
        inspection_date=datetime.now(),
        inspector_name="Michael Inspector",
        photos=[
            InspectionPhoto(filepath=img_path, sha256="fake_photo_sha", captured_at=datetime.now())
        ],
        analyses=[
            PhotoAnalysis(
                filename=img_path.name,
                damage_detected=True,
                damage_type=DamageType.HAIL,
                severity=Severity.SEVERE,
                confidence=0.95,
                confidence_score=0.95,
                alternative_explanation=None,
                hail_hits_visible=True,
                crease_marks=False,
                granule_loss=True,
                exposed_fiberglass=True,
                forensic_narrative="Severe hail impact creasing metal flashings."
            )
        ]
    )

    generator = InspectionReportGenerator()
    pdf_path_str = asyncio.run(generator.generate_homeowner_report(job))
    pdf_path = Path(pdf_path_str)

    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0

def test_supplement_generator_letters_coverage(test_resources, db_job, monkeypatch):
    """Test SupplementGenerator letters paths."""
    job_id = db_job
    monkeypatch.setattr("app.services.pdf.supplement.FIELD_DOCS_DIR", test_resources["docs_dir"])

    job = {
        "id": job_id,
        "homeowner_name": "Jane Homeowner",
        "address_line1": "789 Pine Rd",
        "city": "Valdosta",
        "state": "GA",
        "postal_code": "31602",
        "claim_number": "CLM-999"
    }

    generator = SupplementGenerator()

    # 1. Inspection Letter
    path_str = asyncio.run(generator.generate_inspection_letter(
        job=job,
        ev_data={"total_area_sf": 3200.0, "predominant_pitch": "6/12"},
        inspection_summary={"notes": "Detailed inspection of valley leak."}
    ))
    assert Path(path_str).exists()

    # 2. Rebuttal Letter
    path_str = asyncio.run(generator.generate_rebuttal_letter(
        job=job,
        denial_text="Carrier claims no wind damage seen.",
        rebuttal_narrative="We observed shingle creasing on south slope."
    ))
    assert Path(path_str).exists()

    # 3. Escalation Letter
    path_str = asyncio.run(generator.generate_escalation_letter(
        job=job,
        days_elapsed=15,
        narrative="No response received to the prior supplement package."
    ))
    assert Path(path_str).exists()

def test_supplement_generator_pdf_and_grid_coverage(test_resources, db_job, monkeypatch):
    """Test SupplementGenerator generate_supplement_pdf and generate_evidence_grid."""
    job_id = db_job
    sig_path = test_resources["sig_path"]
    monkeypatch.setattr("app.services.pdf.supplement.FIELD_DOCS_DIR", test_resources["docs_dir"])

    job = {
        "id": job_id,
        "homeowner_name": "Jane Homeowner",
        "address_line1": "789 Pine Rd",
        "city": "Valdosta",
        "state": "GA",
        "postal_code": "31602",
        "claim_number": "CLM-999"
    }

    bom = MaterialBOM(
        field_shingle_bundles=45,
        starter_bundles=3,
        ridge_cap_bundles=4,
        ice_water_rolls=3,
        underlayment_rolls=6,
        drip_edge_pieces=18
    )

    report = DiscrepancyReport(
        job_id=job_id,
        ev_normalized_squares=32.0,
        sol_total_rfg_squares=28.5,
        square_variance=3.5,
        waste_explanation="Waste calculations standard 10%.",
        material_bom=bom,
        discrepancies=[
            Discrepancy(
                category="RIDGE_CAP",
                description="Ridge Cap length variance",
                ev_value=120.0,
                sol_value=90.0,
                variance=30.0,
                code_citation="R905.2.8.2",
                xactimate_code="RFG RIDGC"
            )
        ],
        defensive_narrative="Discrepancy noted on soft metals and ridge length.",
        total_facets=8,
        predominant_pitch="7/12",
        valley_lf=140.0
    )

    generator = SupplementGenerator()

    # 1. generate_supplement_pdf
    path_str = asyncio.run(generator.generate_supplement_pdf(
        report=report,
        narrative="Discrepancy compilation.",
        job=job,
        db_context={}
    ))
    assert Path(path_str).exists()

    # 2. generate_evidence_grid (mocking target job properties)
    class MockEvidenceJob:
        job_id = db_job
        property_address = "789 Pine Rd, Valdosta, GA 31602"
        photos = [
            InspectionPhoto(filepath=test_resources["img_path"], sha256="fake_sha", captured_at=datetime.now())
        ]
        analyses = [
            PhotoAnalysis(
                filename=test_resources["img_path"].name,
                damage_detected=True,
                damage_type=DamageType.HAIL,
                severity=Severity.MODERATE,
                confidence=0.90,
                confidence_score=0.90,
                alternative_explanation=None,
                hail_hits_visible=True,
                crease_marks=False,
                granule_loss=True,
                exposed_fiberglass=False,
                forensic_narrative="Factual hail hits."
            )
        ]

    path_str = asyncio.run(generator.generate_evidence_grid(
        job=MockEvidenceJob(),
        signature_path=str(sig_path)
    ))
    assert Path(path_str).exists()

def test_invoice_generator_coverage(test_resources, db_job, monkeypatch):
    """Test InvoiceGenerator monthly summary and material po generation."""
    job_id = db_job
    monkeypatch.setattr("app.services.pdf.invoice.FIELD_DOCS_DIR", test_resources["docs_dir"])

    job = {
        "id": job_id,
        "homeowner_name": "Jane Homeowner",
        "address_line1": "789 Pine Rd",
        "city": "Valdosta",
        "state": "GA",
        "postal_code": "31602"
    }

    bom = MaterialBOM(
        field_shingle_bundles=45,
        starter_bundles=3,
        ridge_cap_bundles=4,
        ice_water_rolls=3,
        underlayment_rolls=6,
        drip_edge_pieces=18
    )

    generator = InvoiceGenerator()

    # 1. monthly summary
    path_str = asyncio.run(generator.generate_monthly_financial_summary(
        month=8,
        year=2026
    ))
    assert Path(path_str).exists()

    # 2. material po
    path_str = asyncio.run(generator.generate_material_po(
        job=job,
        bom=bom,
        supplier_name="ABC Roofing Supply",
        delivery_date="2026-09-01"
    ))
    assert Path(path_str).exists()
