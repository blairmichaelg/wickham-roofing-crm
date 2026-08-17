import asyncio
import tempfile
from pathlib import Path

import pytest

from app.core.supplement_models import EagleViewData
from app.services.hover_extractor import detect_pdf_format, extract_hover_data, parse_feet_inches


def test_parse_feet_inches():
    assert parse_feet_inches("106' 8\"") == 106.667
    assert parse_feet_inches("23'") == 23.0
    assert parse_feet_inches("-") == 0.0
    assert parse_feet_inches("60' 9\"") == 60.750
    assert parse_feet_inches("0'") == 0.0
    
    with pytest.raises(ValueError, match="Could not parse feet/inches"):
        parse_feet_inches("invalid")

@pytest.mark.asyncio
async def test_hover_extractor_real_file():
    pdf_path = "samples/hover-sample.pdf"
    
    data, sha256 = await extract_hover_data(pdf_path)
    
    assert data.total_area_sf == 2512.0
    assert data.total_facets == 10
    
    assert data.ridge_lf == 106.667
    assert data.hip_lf == 0.0
    assert data.valley_lf == 60.750
    assert data.rake_lf == 151.333
    assert data.eaves_lf == 136.333
    assert data.flashing_lf == 2.833
    assert data.step_flashing_lf == 23.0
    
    assert data.predominant_pitch == "8/12"
    
    # Format detector
    fmt = detect_pdf_format(pdf_path)
    assert fmt == "HOVER"

@pytest.mark.asyncio
async def test_hover_extractor_malformed():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"fake pdf content not valid")
        tmp_path = tmp.name
        
    try:
        with pytest.raises(Exception):
            await extract_hover_data(tmp_path)
    finally:
        Path(tmp_path).unlink()
