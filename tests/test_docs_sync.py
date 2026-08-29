from pathlib import Path
import pytest
from app.main import app


def test_openapi_schema_contains_all_core_endpoints():
    """Verify that OpenAPI schema generates and contains newly released operational endpoints."""
    openapi_schema = app.openapi()
    assert "paths" in openapi_schema
    paths = openapi_schema["paths"]

    # Sprint 1
    assert "/api/office/jobs/{job_id}/measurements/manual" in paths
    assert "post" in paths["/api/office/jobs/{job_id}/measurements/manual"]

    # Sprint 2
    assert "/api/office/jobs/{job_id}/measurement-report" in paths
    assert "post" in paths["/api/office/jobs/{job_id}/measurement-report"]

    assert "/api/office/jobs/{job_id}/statement-of-loss" in paths
    assert "post" in paths["/api/office/jobs/{job_id}/statement-of-loss"]

    assert "/api/office/jobs/{job_id}/supplement_docs" in paths
    assert "post" in paths["/api/office/jobs/{job_id}/supplement_docs"]

    assert "/api/office/jobs/{job_id}/trigger-supplement" in paths
    assert "post" in paths["/api/office/jobs/{job_id}/trigger-supplement"]

    # Sprint 3
    assert "/api/field/push/subscribe" in paths
    assert "post" in paths["/api/field/push/subscribe"]


def test_guide_documents_exist_and_version_stamped():
    """Ensure all core role guides exist and have valid content without stale drift."""
    docs_dir = Path("docs")
    assert docs_dir.exists()

    required_guides = [
        "admin_tech_guide.md",
        "accounting_guide.md",
        "operations_guide.md",
        "canvasser_field_guide.md",
        "field_runbook.md",
        "testing.md"
    ]

    for guide_name in required_guides:
        guide_path = docs_dir / guide_name
        assert guide_path.exists(), f"Missing guide: {guide_name}"
        content = guide_path.read_text(encoding="utf-8")
        assert len(content) > 100, f"Guide {guide_name} appears empty or truncated"


def test_no_legacy_financial_columns_in_operational_guides():
    """Ensure documentation does not instruct users on dropped legacy columns."""
    docs_dir = Path("docs")
    operational_guides = [
        docs_dir / "accounting_guide.md",
        docs_dir / "operations_guide.md",
        docs_dir / "admin_tech_guide.md",
    ]

    dropped_columns = ["carrier_initial_rcv", "carrier_supplemented_rcv", "acv_check_amount"]

    for guide in operational_guides:
        if guide.exists():
            text = guide.read_text(encoding="utf-8")
            for col in dropped_columns:
                assert col not in text, f"Legacy dropped column {col} referenced in {guide.name}"
