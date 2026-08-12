"""
Unit tests for the Smart Code Router (Zero-Cost RAG).
"""

from unittest.mock import patch, MagicMock
from app.core.code_router import parse_code_files, get_relevant_codes
from app.core.supplement_models import DiscrepancyReport, Discrepancy, MaterialBOM, CodeSection

class TestCodeRouter:
    @patch("app.core.code_router.Path")
    def test_parse_code_files(self, mock_path_class):
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        
        mock_file_1 = MagicMock()
        mock_file_1.name = "test1.txt"
        mock_file_1.read_text.return_value = "<SECTION_R100>Content for tag 1.</SECTION_R100>\n<SECTION_R200>\nMultiline\nContent\n</SECTION_R200>"
        
        mock_file_2 = MagicMock()
        mock_file_2.name = "test2.txt"
        mock_file_2.read_text.return_value = "<GEORGIA_AMENDMENTS>\n<GA_R300>Content for tag 3.</GA_R300>\n</GEORGIA_AMENDMENTS>"
        
        mock_path_instance.glob.return_value = [mock_file_1, mock_file_2]
        mock_path_class.return_value = mock_path_instance

        code_index = parse_code_files("fake_dir")
        parse_code_files.cache_clear()
        
        assert "SECTION_R100" in code_index
        assert code_index["SECTION_R100"].text == "Content for tag 1."
        assert code_index["SECTION_R100"].jurisdiction == "National"
        assert code_index["SECTION_R100"].section == "R100"
        
        assert "SECTION_R200" in code_index
        assert "Multiline\nContent" in code_index["SECTION_R200"].text
        
        assert "GA_R300" in code_index
        assert code_index["GA_R300"].text == "Content for tag 3."
        assert code_index["GA_R300"].jurisdiction == "GA"
        assert code_index["GA_R300"].section == "R300"

    def test_get_relevant_codes(self):
        report = DiscrepancyReport(
            job_id="TEST-1",
            ev_normalized_squares=10.0,
            sol_total_rfg_squares=5.0,
            square_variance=5.0,
            waste_explanation="Test",
            material_bom=MaterialBOM(field_shingle_bundles=30, starter_bundles=1, ridge_cap_bundles=1, ice_water_rolls=1, underlayment_rolls=1, drip_edge_pieces=1),
            discrepancies=[
                Discrepancy(category="Missing Drip Edge", description="Test", ev_value=1.0, sol_value=0.0, variance=1.0),
                Discrepancy(category="Missing Ice & Water Shield", description="Test", ev_value=1.0, sol_value=0.0, variance=1.0),
                Discrepancy(category="Unknown Category", description="Test", ev_value=1.0, sol_value=0.0, variance=1.0)
            ]
        )
        
        code_index = {
            "SECTION_R905_2_8_5": CodeSection(code_set="IRC", edition="2024", jurisdiction="National", section="R905.2.8.5", text="Drip edge is required."),
            "GA_R905_2_8_5": CodeSection(code_set="IRC", edition="2024", jurisdiction="GA", section="R905.2.8.5", text="Georgia amendments for drip edge."),
            "SECTION_R905_2_8_2": CodeSection(code_set="IRC", edition="2024", jurisdiction="National", section="R905.2.8.2", text="Ice barrier required."),
            "SOME_OTHER_TAG": CodeSection(code_set="IRC", edition="2024", jurisdiction="National", section="X", text="Not relevant.")
        }
        
        result = get_relevant_codes(report, code_index)
        
        assert "**IRC 2024 Section R905.2.8.5**" in result
        assert "Drip edge is required." in result
        assert "**IRC 2024 (GA Amendments) Section R905.2.8.5**" in result
        assert "Georgia amendments for drip edge." in result
        assert "**IRC 2024 Section R905.2.8.2**" in result
        assert "Ice barrier required." in result
        
        # Verify discrepancy models were mutated
        drip_disc = report.discrepancies[0]
        assert "IRC 2024 (GA Amendments) Section R905.2.8.5" in drip_disc.code_citation
        assert "IRC 2024 Section R905.2.8.5" in drip_disc.code_citation
        
        ice_disc = report.discrepancies[1]
        assert ice_disc.code_citation == "IRC 2024 Section R905.2.8.2"
