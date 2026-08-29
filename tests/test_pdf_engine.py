import io
from pathlib import Path
from unittest.mock import patch

import pytest
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.pdf import constants
from app.services.pdf.engine import (
    NumberedCanvas,
    PDFEngine,
    get_font_name,
    register_brand_fonts,
    truncate_text_to_width,
)


def test_pdf_constants_branding_palettes():
    assert constants.BRAND_NAVY is not None
    assert constants.BRAND_BLUE is not None
    assert "primary" in constants.HOMEOWNER_PALETTE
    assert "primary" in constants.CARRIER_PALETTE
    assert "accent" in constants.NEIGHBOR_PALETTE
    assert "bg_alt" in constants.INTERNAL_PALETTE
    assert constants.MARGIN_DEFAULT == 36


def test_font_registration_fallback():
    # Calling register_brand_fonts when font files are absent should return Helvetica
    family = register_brand_fonts()
    assert family in ("Inter", "Helvetica")
    font_name = get_font_name("normal")
    assert font_name in ("Inter", "Helvetica")
    font_bold = get_font_name("bold")
    assert font_bold in ("Inter-Bold", "Helvetica-Bold")


def test_truncate_text_to_width():
    font_name = get_font_name("normal")
    # Short text should remain unchanged
    short = "Wickham Roofing"
    assert truncate_text_to_width(short, font_name, 10, 200) == short

    # Long text should be truncated with ellipsis
    very_long = "Very Long Company Name That Exceeds The Maximum Available Horizontal Header Width By A Lot"
    truncated = truncate_text_to_width(very_long, font_name, 14, 100)
    assert truncated.endswith("...")
    assert len(truncated) < len(very_long)

    # Empty string check
    assert truncate_text_to_width("", font_name, 10, 100) == ""


def test_numbered_canvas_two_pass_generation():
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    
    styles = getSampleStyleSheet()
    story = []
    # Generate content spanning multiple pages
    for i in range(50):
        story.append(Paragraph(f"Paragraph row {i}", styles["Normal"]))
        story.append(Spacer(1, 20))
    
    doc.build(story, canvasmaker=NumberedCanvas)
    pdf_bytes = buf.getvalue()
    assert len(pdf_bytes) > 0
    assert b"%PDF-" in pdf_bytes


def test_pdf_components_and_audience_styles():
    from app.services.pdf.documents import (
        build_audience_stylesheets,
        create_financial_row,
        create_financial_table,
        create_header,
        create_photo_grid,
        create_section_with_table,
        get_audience_styles,
    )
    
    styles_dict = build_audience_stylesheets()
    assert "homeowner" in styles_dict
    assert "carrier" in styles_dict
    assert "neighbor" in styles_dict
    assert "internal" in styles_dict

    # Test create_header
    hdr = create_header("Test Title", "homeowner", subtitle="Sub")
    assert len(hdr) >= 2

    # Test create_financial_row
    row_t = create_financial_row("Total Amount", "$12,345.67", is_total=True, sub_brand="homeowner")
    assert row_t is not None

    # Test create_financial_table (carrier sub-brand with right-aligned currency)
    data = [
        ["Line Item", "Qty", "Unit Price", "Total RCV"],
        ["Shingles", "30", "$100.00", "$3,000.00"],
    ]
    table = create_financial_table(data, [150, 50, 100, 100], sub_brand="carrier", currency_cols=[2, 3], has_header=True)
    assert table is not None

    # Test create_section_with_table
    sect = create_section_with_table("Section 1", table, sub_brand="carrier")
    assert sect is not None

    # Test create_photo_grid
    photos = [
        {"path": "nonexistent.jpg", "caption": "Test Damage Signal"},
    ]
    grid = create_photo_grid(photos, cols=2, sub_brand="homeowner")
    assert grid is not None

