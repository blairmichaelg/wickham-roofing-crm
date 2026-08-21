"""
V4 Deterministic PDF extractor for EagleView Premium Roof Reports.
Uses precise Regex patterns to extract exact metrics for QuickBooks math.
Raises ValueError immediately if patterns fail due to layout changes.
"""

import asyncio
import re
from pathlib import Path

import pdfplumber
import structlog

from app.core.supplement_models import EagleViewData

logger = structlog.get_logger("app.services.pdf_extractor")

def _parse_strict_float(text: str, pattern: str, metric_name: str) -> float:
    """Helper to enforce strict regex extraction with clear ValueError."""
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        raise ValueError(f"Failed to parse EagleView metric: {metric_name}")
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        raise ValueError(f"Extracted invalid float for metric: {metric_name}")

async def extract_eagleview_data(pdf_path: str | Path) -> tuple[EagleViewData, str]:
    """
    Extract structured measurement data from an EagleView Premium Roof Report PDF.
    Only focuses on the 4 metrics needed for V4 CRM QBO Export.
    """
    pdf_path = Path(pdf_path)
    log = logger.bind(pdf_path=str(pdf_path))

    if not pdf_path.exists():
        log.error("eagleview_pdf_not_found")
        raise FileNotFoundError(f"EagleView PDF not found: {pdf_path}")

    log.info("eagleview_extraction_started")

    def _extract() -> tuple[EagleViewData, str]:
        import hashlib
        sha256_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        
        with pdfplumber.open(str(pdf_path)) as pdf:
            # We must search all pages because we don't know exactly where the summary is
            full_text = []
            for page in pdf.pages:
                extracted = page.extract_text()
                if extracted:
                    full_text.append(extracted)
            
            combined_text = "\n".join(full_text)
            
            # Directive 1: Strict Regex parsing
            total_area = _parse_strict_float(
                combined_text,
                r"Total\s+Roof\s+Area\s*=\s*([\d,]+(?:\.\d+)?)\s*sq\s*ft",
                "Total Roof Area"
            )
            
            ridges = _parse_strict_float(
                combined_text,
                r"Ridges\s*=\s*([\d,]+(?:\.\d+)?)\s*ft",
                "Total Ridge Length"
            )
            
            valleys = _parse_strict_float(
                combined_text,
                r"Valleys\s*=\s*([\d,]+(?:\.\d+)?)\s*ft",
                "Total Valley Length"
            )
            
            eaves = _parse_strict_float(
                combined_text,
                r"Eaves(?:\/Starter)?\*?\*?\s*=\s*([\d,]+(?:\.\d+)?)\s*ft",
                "Eaves Length"
            )
            
            rakes = _parse_strict_float(
                combined_text,
                r"Rakes\*?\*?\s*=\s*([\d,]+(?:\.\d+)?)\s*ft",
                "Rakes Length"
            )

            hips = _parse_strict_float(
                combined_text,
                r"Hips\*?\*?\s*=\s*([\d,]+(?:\.\d+)?)\s*ft",
                "Hip Length"
            )

            facets_match = re.search(r"Facets\s*=\s*([\d,]+)", combined_text, re.IGNORECASE)
            total_facets = int(facets_match.group(1).replace(",", "")) if facets_match else 0

            pitch_match = re.search(
                r"(?:Predominant|Primary)\s+Pitch\s*[=:]\s*([\d]+/12)",
                combined_text,
                re.IGNORECASE
            )
            if not pitch_match:
                raise ValueError(
                    "Failed to parse EagleView metric: Predominant Pitch. "
                    "Upload a Premium or Full Hover report with pitch data."
                )
            predominant_pitch = pitch_match.group(1)

            return EagleViewData(
                total_area_sf=total_area,
                rake_lf=rakes,
                valley_lf=valleys,
                ridge_lf=ridges,
                hip_lf=hips,
                eaves_lf=eaves,
                drip_edge_lf=eaves + rakes,
                flashing_lf=None,
                step_flashing_lf=None,
                total_facets=total_facets,

                predominant_pitch=predominant_pitch
            ), sha256_hash

    result, sha256_hash = await asyncio.to_thread(_extract)
    log.info("eagleview_extraction_complete", sha256=sha256_hash)
    return result, sha256_hash
