"""
Hover PDF Extractor.
Parses Hover measurements reports into EagleViewData formats.
"""

import asyncio
import re
from pathlib import Path

import pdfplumber
import structlog

from app.core.supplement_models import EagleViewData

logger = structlog.get_logger("app.services.hover_extractor")

def parse_feet_inches(text: str) -> float:
    """
    Converts Hover feet-inches strings (e.g. "106' 8\"" or "23'") to decimal feet.
    A dash "-" represents 0.0.
    """
    text = text.strip()
    if text == "-":
        return 0.0
    
    feet = 0.0
    inches = 0.0
    
    feet_match = re.search(r"(\d+)'", text)
    if feet_match:
        feet = float(feet_match.group(1))
        
    inch_match = re.search(r"(\d+)\"", text)
    if inch_match:
        inches = float(inch_match.group(1))
        
    if not feet_match and not inch_match:
        raise ValueError(f"Could not parse feet/inches from: {text}")
        
    return round(feet + (inches / 12.0), 3)

def _parse_hover_metric(text: str, pattern: str, metric_name: str) -> float:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        raise ValueError(f"Failed to parse Hover metric: {metric_name}")
    try:
        return parse_feet_inches(match.group(1))
    except ValueError:
        raise ValueError(f"Extracted invalid measurement for metric: {metric_name}")

def detect_pdf_format(pdf_path: str | Path) -> str:
    """
    Reads page 1 of a PDF to classify it as EAGLEVIEW or HOVER.
    """
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            if not pdf.pages:
                return "UNKNOWN"
            page_1_text = pdf.pages[0].extract_text() or ""
            
            if "HOVER Inc." in page_1_text and "Roof Measurements" in page_1_text:
                return "HOVER"
            elif "EagleView" in page_1_text or "Premium Roof Report" in page_1_text:
                return "EAGLEVIEW"
            else:
                return "UNKNOWN"
    except Exception:
        return "UNKNOWN"

async def extract_hover_data(pdf_path: str | Path) -> tuple[EagleViewData, str]:
    """
    Extract structured measurement data from a Hover Roof Measurements PDF.
    """
    pdf_path = Path(pdf_path)
    log = logger.bind(pdf_path=str(pdf_path))

    if not pdf_path.exists():
        log.error("hover_pdf_not_found")
        raise FileNotFoundError(f"Hover PDF not found: {pdf_path}")

    log.info("hover_extraction_started")

    def _extract():
        import hashlib
        sha256_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        
        with pdfplumber.open(str(pdf_path)) as pdf:
            full_text = []
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    full_text.append(extracted)
            
            combined_text = "\n".join(full_text)
            
            # Area and Facets
            area_match = re.search(r"Roof Facets\s+([\d,]+)\s*ft²\s+(\d+)", combined_text, re.IGNORECASE)
            if not area_match:
                # Try fallback for area (Page 8 summary)
                area_match = re.search(r"Total\s+(\d+)\s+([\d,]+)\s*ft²", combined_text, re.IGNORECASE)
                if not area_match:
                    raise ValueError("Failed to parse Hover metric: Roof Facets and Area")
                total_facets = int(area_match.group(1).replace(",", ""))
                total_area = float(area_match.group(2).replace(",", ""))
            else:
                total_area = float(area_match.group(1).replace(",", ""))
                total_facets = int(area_match.group(2).replace(",", ""))
            
            ridges = _parse_hover_metric(combined_text, r"Ridges\s*\(RI\)\s+(.+)", "Total Ridge Length")
            hips = _parse_hover_metric(combined_text, r"Hips\s*\(H\)\s+(.+)", "Hip Length")
            valleys = _parse_hover_metric(combined_text, r"Valleys\s*\(V\)\s+(.+)", "Total Valley Length")
            rakes = _parse_hover_metric(combined_text, r"Rakes\s*\(RA\)\s+(.+)", "Rakes Length")
            eaves = _parse_hover_metric(combined_text, r"Eaves\s*\(E\)\s+(.+)", "Eaves Length")
            flashing = _parse_hover_metric(combined_text, r"Flashing\s*\(F\)\*?\s+(.+)", "Flashing Length")
            step_flashing = _parse_hover_metric(combined_text, r"Step Flashing\s*\(SF\)\*?\s+(.+)", "Step Flashing Length")
            
            # Pitch
            pitch_match = re.search(r"(\d+)\s*/\s*12\s+[\d,]+\s*ft²\s+100%", combined_text, re.IGNORECASE)
            if not pitch_match:
                # General fallback for pitch
                pitch_match = re.search(r"(\d+)\s*/\s*12", combined_text, re.IGNORECASE)
                if not pitch_match:
                    raise ValueError("Failed to parse Hover metric: Predominant Pitch")
                
            predominant_pitch = f"{pitch_match.group(1)}/12"
            
            return EagleViewData(
                total_area_sf=total_area,
                rake_lf=rakes,
                valley_lf=valleys,
                ridge_lf=ridges,
                hip_lf=hips,
                eaves_lf=eaves,
                drip_edge_lf=eaves + rakes,
                flashing_lf=flashing,
                step_flashing_lf=step_flashing,
                total_facets=total_facets,
                predominant_pitch=predominant_pitch
            ), sha256_hash

    result, sha256_hash = await asyncio.to_thread(_extract)
    log.info("hover_extraction_complete", sha256=sha256_hash)
    return result, sha256_hash
