"""
Unit tests for the V4 EagleView strict Regex PDF extractor.
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.pdf_extractor import extract_eagleview_data


# ---------------------------------------------------------------------------
# Test: Async extraction function
# ---------------------------------------------------------------------------
class TestExtractEagleviewData:
    """Tests for the strict Regex extraction entry point."""

    def test_file_not_found_raises(self):
        """Should raise FileNotFoundError for missing PDF."""
        with pytest.raises(FileNotFoundError):
            asyncio.run(extract_eagleview_data("/nonexistent/path.pdf"))

    @patch("app.services.pdf_extractor.pdfplumber.open")
    @patch("app.services.pdf_extractor.Path.exists", return_value=True)
    @patch("app.services.pdf_extractor.Path.read_bytes", return_value=b"fake data")
    def test_successful_extraction(self, mock_read_bytes, mock_exists, mock_pdfplumber_open):
        """Should extract the required metrics successfully."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = (
            "Total Roof Area = 3,000.5 sq ft\n"
            "Ridges = 50.0 ft\n"
            "Valleys = 75 ft\n"
            "Rakes = 40 ft\n"
            "Eaves** = 80 ft\n"
            "Hips = 10 ft\n"
            "Facets = 8\n"
            "Predominant Pitch = 6/12\n"
        )

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdfplumber_open.return_value = mock_pdf

        result, sha256 = asyncio.run(extract_eagleview_data("fake.pdf"))

        assert result.total_area_sf == 3000.5
        assert result.ridge_lf == 50.0
        assert result.valley_lf == 75.0
        assert result.rake_lf == 40.0
        assert result.eaves_lf == 80.0
        assert result.hip_lf == 10.0
        assert result.drip_edge_lf == 120.0
        assert result.flashing_lf is None
        assert result.step_flashing_lf is None
        assert result.total_facets == 8
        assert result.predominant_pitch == "6/12"

    @patch("app.services.pdf_extractor.pdfplumber.open")
    @patch("app.services.pdf_extractor.Path.exists", return_value=True)
    @patch("app.services.pdf_extractor.Path.read_bytes", return_value=b"fake data")
    def test_missing_metric_raises_value_error(self, mock_read_bytes, mock_exists, mock_pdfplumber_open):
        """Should raise ValueError if any of the required patterns fail."""
        mock_page = MagicMock()
        # Missing Ridges
        mock_page.extract_text.return_value = (
            "Total Roof Area = 3,000.5 sq ft\n"
            "Valleys = 75 ft\n"
            "Rakes = 40 ft\n"
            "Eaves** = 80 ft\n"
        )

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__.return_value = mock_pdf
        mock_pdfplumber_open.return_value = mock_pdf

        with pytest.raises(ValueError, match="Failed to parse EagleView metric: Total Ridge Length"):
            asyncio.run(extract_eagleview_data("fake.pdf"))


# ---------------------------------------------------------------------------
# Test: Real PDF extraction (integration test)
# ---------------------------------------------------------------------------
class TestRealPDFExtraction:
    """Integration test using the actual sample EagleView PDF."""

    SAMPLE_PDF = Path("samples/EagleView-Sample-Premium_Roof_Report.pdf")

    @pytest.mark.skipif(
        not Path("samples/EagleView-Sample-Premium_Roof_Report.pdf").exists(),
        reason="Sample EagleView PDF not available",
    )
    def test_extract_from_real_eagleview_pdf(self):
        """Validate extraction against known values from the sample report."""
        result, sha256 = asyncio.run(extract_eagleview_data(str(self.SAMPLE_PDF)))

        assert result.total_area_sf == 6788.0
        assert result.ridge_lf == 120.0
        assert result.valley_lf == 288.0
        assert result.rake_lf == 114.0
        assert result.eaves_lf == 276.0
        # Check derived
        assert result.drip_edge_lf == 390.0
