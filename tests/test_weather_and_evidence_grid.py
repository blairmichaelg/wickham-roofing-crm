import uuid
import pytest
from pathlib import Path

from app.core.database import update_job_claim_info, get_connection, update_job_status
from app.services.pdf import PDFGenerator
from app.api.field_routes import get_inspection_summary


@pytest.mark.asyncio
async def test_generate_evidence_grid_with_photos_on_disk(tmp_path):
    """Verify evidence grid generates correctly even when job.analyses is empty."""
    job_id = str(uuid.uuid4())
    
    # Create fake job directory with a dummy photo
    job_photos_dir = Path("data/field_photos") / job_id
    job_photos_dir.mkdir(parents=True, exist_ok=True)
    photo_file = job_photos_dir / "test_photo_01.jpg"
    
    # Create valid minimal JPEG file
    photo_file.write_bytes(
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x10\x00\x10\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'
    )
    
    job_data = {
        "job_id": job_id,
        "homeowner_name": "Test Homeowner",
        "property_address": "123 Main St, Valdosta, GA",
        "inspector_name": "Test Inspector",
        "photos": [],
        "analyses": []
    }
    
    pdf_gen = PDFGenerator()
    out_path = await pdf_gen.generate_evidence_grid(job_data)
    
    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0
    
    # Cleanup
    try:
        photo_file.unlink(missing_ok=True)
        job_photos_dir.rmdir()
        Path(out_path).unlink(missing_ok=True)
    except Exception:
        pass


def test_update_claim_info_database():
    """Verify update_job_claim_info updates loss_date and insurer_name cleanly."""
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, postal_code, address_line1, city, state, phone, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, "Test Owner", "31602", "123 Main St", "Valdosta", "GA", "555-1234", "owner@test.com")
        )
        conn.commit()
    finally:
        conn.close()
    
    update_job_claim_info(
        job_id=job_id,
        insurer_name="State Farm",
        loss_date="2026-07-29",
        claim_number="CL-987654",
        policy_number="POL-123456",
        adjuster_name="John Adjuster",
        adjuster_phone="555-0199",
        adjuster_email="adjuster@statefarm.com"
    )
    
    # Verify in DB
    conn = get_connection()
    try:
        row = conn.execute("SELECT insurer_name, claim_number, policy_type FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["insurer_name"] == "State Farm"
        assert row["claim_number"] == "CL-987654"
        assert row["policy_type"] == "POL-123456"
        
        storm_row = conn.execute("SELECT loss_date FROM storm_verifications WHERE job_id = ?", (job_id,)).fetchone()
        assert storm_row["loss_date"] == "2026-07-29"
    finally:
        conn.close()


def test_naked_lead_lifecycle_and_conversion():
    """Verify a naked lead is created as LEAD_CAPTURED and converts to CONTINGENCY_SIGNED upon signature."""
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        # 1. Create Naked Lead (No Signature)
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, "Naked Lead Owner", "456 Oak St", "Valdosta", "GA", "31602", "555-9999", "LEAD_CAPTURED", "Sales Rep John")
        )
        conn.commit()
        
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["status"] == "LEAD_CAPTURED"
        
        # 2. Advance job via signature update
        update_job_status(job_id, "CONTINGENCY_SIGNED", "Contingency signed by Naked Lead Owner")
        
        row_signed = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row_signed["status"] == "CONTINGENCY_SIGNED"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_generate_evidence_grid_fallback_caption(tmp_path):
    """Verify that a photo with NO cached AI analysis gets the correct fallback caption in the Evidence Grid."""
    import uuid
    from PIL import Image as PILImage
    from pdfminer.high_level import extract_text
    
    job_id = str(uuid.uuid4())
    
    # Create fake job directory with a dummy photo
    job_photos_dir = Path("data/field_photos") / job_id
    job_photos_dir.mkdir(parents=True, exist_ok=True)
    photo_file = job_photos_dir / "test_photo_01.jpg"
    
    # Save a valid dummy JPEG using PIL
    img = PILImage.new("RGB", (100, 100), color="blue")
    img.save(photo_file, format="JPEG")
    
    job_data = {
        "job_id": job_id,
        "homeowner_name": "Test Fallback Owner",
        "property_address": "123 Main St, Valdosta, GA",
        "inspector_name": "Test Fallback Inspector",
        "photos": [],
        "analyses": []
    }
    
    pdf_gen = PDFGenerator()
    out_path = await pdf_gen.generate_evidence_grid(job_data)
    
    assert Path(out_path).exists()
    assert Path(out_path).stat().st_size > 0
    
    # Extract text from the generated PDF and assert the correct fallback caption/note
    pdf_text = extract_text(out_path)
    pdf_text_norm = " ".join(pdf_text.split())
    
    # Asserts the caption text is "No AI Analysis" (not "Pending Analysis")
    assert "No AI Analysis" in pdf_text_norm
    assert "Pending Analysis" not in pdf_text_norm
    
    # Asserts the note text contains "No AI vision analysis is currently available for this photo"
    # (not "Awaiting AI Audit" / "pending automated vision analysis")
    assert "No AI vision analysis is currently available for this photo" in pdf_text_norm
    assert "Awaiting AI Audit" not in pdf_text_norm
    assert "pending automated vision analysis" not in pdf_text_norm
    
    # Cleanup
    try:
        photo_file.unlink(missing_ok=True)
        job_photos_dir.rmdir()
        Path(out_path).unlink(missing_ok=True)
    except Exception:
        pass

