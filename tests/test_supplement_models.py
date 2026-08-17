"""
Unit tests for the supplement Pydantic models.

Tests the computed fields, null handling, and data validation
for the InsurTech Supplement Engine's data contracts.
"""

from app.core.supplement_models import (
    Discrepancy,
    DiscrepancyReport,
    EagleViewData,
    LineItem,
    MaterialBOM,
    StatementOfLoss,
)


class TestEagleViewData:
    """Tests for the EagleViewData model and its computed fields."""

    def test_eagleview_data_creation(self):
        """Test basic creation without waste_factor."""
        data = EagleViewData(
            total_area_sf=2500.0,
            rake_lf=50.0,
            valley_lf=30.0,
            ridge_lf=40.0,
            hip_lf=60.0,
            eaves_lf=100.0,
            drip_edge_lf=150.0,
            flashing_lf=5.0,
            step_flashing_lf=10.0,
            total_facets=8,
            predominant_pitch="6/12",
        )
        assert data.total_area_sf == 2500.0

class TestMaterialBOM:
    """Tests for the MaterialBOM model."""

    def test_bom_creation(self):
        bom = MaterialBOM(
            field_shingle_bundles=100,
            starter_bundles=4,
            ridge_cap_bundles=3,
            ice_water_rolls=2,
            underlayment_rolls=4,
            drip_edge_pieces=5,
        )
        assert bom.field_shingle_bundles == 100
        assert bom.drip_edge_pieces == 5


class TestLineItem:
    """Tests for LineItem null handling."""

    def test_line_item_with_all_fields(self):
        item = LineItem(
            trade="RFG",
            code="RFG 300",
            description="Shingle roofing",
            quantity=25.0,
            unit_of_measure="SQ",
            unit_price=85.50,
        )
        assert item.quantity == 25.0

    def test_line_item_with_null_fields(self):
        """SoLs may omit quantity or price — model must accept None."""
        item = LineItem(
            trade="RFG",
            code="RFG 300",
            description="Shingle roofing",
            quantity=None,
            unit_of_measure=None,
            unit_price=None,
        )
        assert item.quantity is None
        assert item.unit_price is None


class TestStatementOfLoss:
    """Tests for StatementOfLoss model."""

    def test_empty_line_items_default(self):
        sol = StatementOfLoss()
        assert sol.line_items == []
        assert sol.carrier_name is None

    def test_with_line_items(self):
        sol = StatementOfLoss(
            carrier_name="State Farm",
            claim_number="CLM-12345",
            line_items=[
                LineItem(trade="RFG", code="RFG 300", description="Shingles", quantity=25.0),
            ],
            overhead_and_profit_included=True,
        )
        assert len(sol.line_items) == 1
        assert sol.overhead_and_profit_included is True


class TestDiscrepancyReport:
    """Tests for the DiscrepancyReport output model."""

    def test_clean_report(self):
        report = DiscrepancyReport(
            job_id="test_123",
            ev_normalized_squares=78.06,
            sol_total_rfg_squares=78.0,
            square_variance=0.06,
            waste_explanation="15%",
            material_bom=MaterialBOM(field_shingle_bundles=10, starter_bundles=1, ridge_cap_bundles=1, ice_water_rolls=1, underlayment_rolls=1, drip_edge_pieces=1)
        )
        assert report.discrepancies == []
        assert report.square_variance == 0.06

    def test_report_with_discrepancies(self):
        report = DiscrepancyReport(
            job_id="test_456",
            ev_normalized_squares=78.06,
            sol_total_rfg_squares=65.0,
            square_variance=13.06,
            waste_explanation="15%",
            material_bom=MaterialBOM(field_shingle_bundles=10, starter_bundles=1, ridge_cap_bundles=1, ice_water_rolls=1, underlayment_rolls=1, drip_edge_pieces=1),
            discrepancies=[
                Discrepancy(
                    category="Area Shortage",
                    description="SoL underestimates roof area by 13.06 SQ",
                    ev_value=78.06,
                    sol_value=65.0,
                    variance=13.06,
                ),
            ],
        )
        assert len(report.discrepancies) == 1
        assert report.discrepancies[0].category == "Area Shortage"
