"""
Unit and integration tests for PDF layout containment, NumberedCanvas pagination,
and AI visual provenance rendering across document generators.

Verifies:
1. Unbounded/massive AI narrative strings render cleanly without LayoutError or overflow
   in at least 3 generators (supplement.py, neighbor_letter.py, evidence_packet.py).
2. NumberedCanvas produces consistent two-pass 'Page X of Y' footers across multi-page builds.
3. ProvenanceString content is rendered with AI disclaimer and styling, whereas deterministic
   factual fields remain unmarked.
4. Prohibited sales promise text is blocked before it can reach a PDF generator.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pdfplumber
import pytest

from app.core.supplement_models import Discrepancy, DiscrepancyReport, MaterialBOM
from app.services.ai.guardrails import SalesPromiseViolationError
from app.services.ai.provenance import ProvenanceString
from app.services.pdf.evidence_packet import EvidencePacketGenerator
from app.services.pdf.neighbor_letter import NeighborLetterGenerator
from app.services.pdf.supplement import SupplementGenerator


@pytest.fixture
def supplement_gen() -> SupplementGenerator:
    return SupplementGenerator()


@pytest.fixture
def neighbor_gen() -> NeighborLetterGenerator:
    return NeighborLetterGenerator()


@pytest.fixture
def evidence_gen() -> EvidencePacketGenerator:
    return EvidencePacketGenerator()


@pytest.fixture
def sample_job_dict() -> dict:
    return {
        "id": "JOB-PDF-TEST-001",
        "homeowner_name": "Eleanor Vance",
        "property_address": "404 Peachtree Ridge, Thomasville, GA 31792",
        "address_line1": "404 Peachtree Ridge",
        "city": "Thomasville",
        "state": "GA",
        "postal_code": "31792",
        "claim_number": "CLM-GA-992144",
        "insurance_carrier": "State Farm",
        "insurer_name": "State Farm",
        "policy_type": "HO-3 Replacement Cost",
        "status": "INSPECTION_COMPLETED",
        "inspector_name": "Marcus Vance",
        "canvasser_name": "Tyler Cole",
        "phone": "229-555-0199",
    }


@pytest.fixture
def sample_discrepancy_report() -> DiscrepancyReport:
    return DiscrepancyReport(
        job_id="JOB-PDF-TEST-001",
        ev_normalized_squares=32.5,
        sol_total_rfg_squares=28.0,
        square_variance=4.5,
        waste_explanation="Complex hip/valley roof geometry requires 15% waste factor.",
        material_bom=MaterialBOM(
            field_shingle_bundles=98,
            starter_bundles=7,
            ridge_cap_bundles=3,
            ice_water_rolls=2,
            underlayment_rolls=4,
            drip_edge_pieces=22,
        ),
        discrepancies=[
            Discrepancy(
                category="Ridge Cap",
                description="Under-measured ridge cap in carrier scope",
                ev_value=75.0,
                sol_value=40.0,
                variance=35.0,
                xactimate_code="RFG RIDGC+",
                code_citation="IRC Section R905.2.8.2",
            ),
            Discrepancy(
                category="Drip Edge",
                description="Omitted perimeter drip edge metal",
                ev_value=220.0,
                sol_value=0.0,
                variance=220.0,
                xactimate_code="RFG DRIP",
                code_citation="IRC Section R905.2.8.5",
            ),
        ],
    )


def test_supplement_pdf_handles_massive_ai_narrative_without_layout_error(
    supplement_gen: SupplementGenerator,
    sample_job_dict: dict,
    sample_discrepancy_report: DiscrepancyReport,
    tmp_path: Path,
) -> None:
    """
    Asserts SupplementGenerator renders an artificially massive AI narrative string
    (6,000+ characters) cleanly inside KeepInFrame without raising Platypus LayoutError.
    """
    massive_ai_text = (
        "Technical forensic justification for supplemental line items pursuant to 2021 IRC: "
        "The subject property exhibits widespread mechanical wind uplift and hail impact fractures "
        "along the north and west roof slopes exceeding ASTM D3462 deflection tolerances. "
        "Per manufacturer specifications (GAF Timberline HDZ Technical Bulletin #21), shingles once displaced "
        "cannot be reliably re-adhered without compromising thermal seal warranty. "
        "Drip edge flashing is mandated along eaves and rakes pursuant to IRC Section R905.2.8.5. "
    ) * 20

    prov_string = ProvenanceString(
        text=massive_ai_text,
        source_refs=["job_id:JOB-PDF-TEST-001", "rule:rfg_drip"],
        guardrail_passed=True,
        guardrail_checks=["verify_prohibited_sales_promises", "verify_building_code_citations"],
        model_name="gemini-2.5-flash",
        prompt_version_hash="test_hash_12345",
    )

    db_ctx = {
        "rules": [
            {
                "citation_type": "IRC",
                "citation_text": "Drip edge is required at eaves and gables.",
                "climate_dependent": False,
                "required_child_code": "RFG DRIP",
                "quantity_delta": 22.0,
            }
        ],
        "jurisdiction_code_version": "2021_IRC",
    }

    pdf_path = asyncio.run(
        supplement_gen.generate_supplement_pdf(
            report=sample_discrepancy_report,
            narrative=prov_string,
            job=sample_job_dict,
            db_context=db_ctx,
        )
    )

    assert Path(pdf_path).exists()
    assert Path(pdf_path).stat().st_size > 5000

    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        assert num_pages >= 1

        # Check footer on each page for "Page X of Y"
        all_text = ""
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text += text
            expected_page_str = f"Page {i} of {num_pages}"
            assert expected_page_str in text, f"Page {i} missing '{expected_page_str}'"

        # Verify AI disclaimer line was injected by build_ai_provenance_flowable
        assert "AI-assisted technical justification" in all_text
        # Verify hard factual figures are preserved accurately
        assert "CLM-GA-992144" in all_text
        assert "RFG DRIP" in all_text


def test_neighbor_letter_handles_long_narrative_and_numbered_canvas(
    neighbor_gen: NeighborLetterGenerator,
    sample_job_dict: dict,
    tmp_path: Path,
) -> None:
    """
    Asserts NeighborLetterGenerator handles long customized narrative text gracefully,
    incorporates the visual provenance indicator, and renders with NumberedCanvas.
    """
    long_narrative = (
        "We are actively restoring several storm-damaged roofs on your street following the recent severe weather. "
        "Our Haag-certified inspectors have identified extensive impact fractures that compromise waterproof integrity. "
        "We encourage all immediate neighbors to schedule an inspection before secondary moisture damage develops."
    ) * 5

    prov_narrative = ProvenanceString(
        text=long_narrative,
        source_refs=["job_id:JOB-PDF-TEST-001"],
        guardrail_passed=True,
        guardrail_checks=["verify_prohibited_sales_promises"],
        model_name="gemini-2.5-flash",
        prompt_version_hash="prompt_hash_nb_01",
    )

    storm_events = [
        {
            "event_type": "Hailstorm",
            "county": "Thomas County",
            "hail_size_inches": 1.75,
            "wind_speed_mph": 65,
            "report_time_utc": "2026-08-15 14:00:00",
        }
    ]

    pdf_path = asyncio.run(
        neighbor_gen.generate(
            job=sample_job_dict,
            storm_events=storm_events,
            narrative=prov_narrative,
        )
    )

    assert Path(pdf_path).exists()
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        assert num_pages >= 1

        all_text = ""
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text += text
            assert f"Page {i} of {num_pages}" in text

        assert "AI-assisted neighborhood notice" in all_text
        assert "1.75-inch hail" in all_text


def test_evidence_packet_handles_long_field_observations_and_numbered_canvas(
    evidence_gen: EvidencePacketGenerator,
    sample_job_dict: dict,
    tmp_path: Path,
) -> None:
    """
    Asserts EvidencePacketGenerator wraps lengthy field observation narratives
    in wrap_keep_in_frame and produces consistent NumberedCanvas page numbering.
    """
    import sqlite3
    import app.core.database

    test_db = str(app.core.database.get_db_path())

    # Seed job and exhibit into test db
    with sqlite3.connect(test_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (id, homeowner_name, address_line1, city, state, postal_code,
                                         phone, claim_number, insurer_name, policy_type, status, inspector_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                sample_job_dict["id"],
                sample_job_dict["homeowner_name"],
                sample_job_dict["address_line1"],
                sample_job_dict["city"],
                sample_job_dict["state"],
                sample_job_dict["postal_code"],
                sample_job_dict["phone"],
                sample_job_dict["claim_number"],
                sample_job_dict["insurer_name"],
                sample_job_dict["policy_type"],
                sample_job_dict["status"],
                sample_job_dict["inspector_name"],
            ),
        )
        conn.commit()

    # Add massive observation text (over 2,000 chars)
    huge_observation = (
        "Extensive directional hail spatter and granular dislodgement observed on 30-year laminated shingles. "
        "Micro-fissuring penetrates fiberglass matting in test square #1 (North elevation, 8/12 slope). "
        "Soft metal indentations on box vents and lead pipe boots measure up to 1.5 inches in diameter. "
    ) * 12

    from app.services.evidence_matrix import create_evidence_exhibit

    create_evidence_exhibit(
        job_id=sample_job_dict["id"],
        category="shingle_damage",
        observation=huge_observation,
        location_roof_area="North Slope - Test Square 1",
        proposed_claim_line_ref="RFG 300S",
        status="included",
        db_path=test_db,
    )

    pdf_path = evidence_gen.generate_packet_sync(
        job_id=sample_job_dict["id"],
        version=1,
        included_only=False,
        db_path=test_db,
    )

    assert Path(pdf_path).exists()
    with pdfplumber.open(pdf_path) as pdf:
        num_pages = len(pdf.pages)
        assert num_pages >= 1

        all_text = ""
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            all_text += text
            assert f"Page {i} of {num_pages}" in text

        assert "SUPPLEMENT EVIDENCE PACKET" in all_text
        assert "CLM-GA-992144" in all_text
        assert "North Slope - Test Square 1" in all_text


def test_prohibited_sales_promise_blocked_before_pdf_render() -> None:
    """
    Asserts that text with prohibited sales promises cannot be turned into a ProvenanceString
    and will raise SalesPromiseViolationError before reaching any PDF generator.
    """
    from app.services.ai.provenance import enforce_provenance

    @enforce_provenance(prompt_name="TEST_PROMPT")
    async def mock_generate_bad_narrative() -> str:
        return "We guarantee your insurance claim will be approved and we will cover your deductible in full."

    with pytest.raises(SalesPromiseViolationError):
        asyncio.run(mock_generate_bad_narrative())
