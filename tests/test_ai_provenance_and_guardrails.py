"""
Tests for AI Provenance Contract, Guardrail Enforcement Chokepoint, and Prompt Versioning.

Validates that:
1. Every AI text-generation call returning free text is wrapped by @enforce_provenance.
2. Prohibited sales language (deductible waiving, free roof, carrier coverage guarantees)
   is loudly blocked with SalesPromiseViolationError BEFORE reaching any document or caller.
3. Compliant text returns ProvenanceString with guardrail_passed=True, model metadata,
   and stable cryptographic prompt version hashes.
4. Prompt constants in prompts.py have deterministic, verifiable version hashes.
5. Cache keys are strictly isolated by prompt version hash.
"""

import hashlib
import pytest
from unittest.mock import AsyncMock, patch

from app.services.ai.guardrails import (
    SalesPromiseViolationError,
    verify_prohibited_sales_promises,
)
from app.services.ai.prompts import (
    BATCH_ROOF_PHOTO_INSPECTION_PROMPT,
    DOOR_SCRIPT_SYSTEM_PROMPT,
    PROMPT_REGISTRY,
    SALES_SUMMARY_SYSTEM_PROMPT,
    compute_prompt_hash,
    get_prompt_version_hash,
)
from app.services.ai.provenance import (
    ProvenanceString,
    enforce_provenance,
)
from app.services.ai_service import GeminiClient
from app.core.cache import (
    get_cached_analysis,
    set_cached_analysis,
    init_db,
)
from app.core.inspection_models import DamageType, PhotoAnalysis, Severity


class TestPromptVersioning:
    """Test cryptographic prompt hashing and registry integrity."""

    def test_compute_prompt_hash_is_deterministic(self):
        h1 = compute_prompt_hash("Test prompt text")
        h2 = compute_prompt_hash("Test prompt text")
        assert h1 == h2
        assert len(h1) == 16
        expected = hashlib.sha256("Test prompt text".encode("utf-8")).hexdigest()[:16]
        assert h1 == expected

    def test_prompt_registry_contains_critical_prompts(self):
        assert "SALES_SUMMARY_SYSTEM_PROMPT" in PROMPT_REGISTRY
        assert "DOOR_SCRIPT_SYSTEM_PROMPT" in PROMPT_REGISTRY
        assert "BATCH_ROOF_PHOTO_INSPECTION_PROMPT" in PROMPT_REGISTRY

    def test_get_prompt_version_hash_resolution(self):
        h_by_name = get_prompt_version_hash("SALES_SUMMARY_SYSTEM_PROMPT")
        h_by_text = get_prompt_version_hash(SALES_SUMMARY_SYSTEM_PROMPT)
        assert h_by_name == h_by_text
        assert len(h_by_name) == 16


class TestProvenanceContractAndGuardrailChokepoint:
    """Test that @enforce_provenance acts as an inescapable chokepoint."""

    @pytest.mark.asyncio
    async def test_prohibited_sales_promise_raises_violation_error(self):
        """Prohibited deductible waiving or guarantee must raise SalesPromiseViolationError."""

        @enforce_provenance(prompt_name="SALES_SUMMARY_SYSTEM_PROMPT")
        async def fake_generator():
            return "Don't worry about the cost, we will waive your deductible and guarantee insurance approval!"

        with pytest.raises(SalesPromiseViolationError) as exc_info:
            await fake_generator()

        violations = exc_info.value.violations
        assert len(violations) >= 1
        assert any("deductible" in v.lower() or "guarantee" in v.lower() for v in violations)

    @pytest.mark.asyncio
    async def test_free_roof_claim_raises_violation_error(self):
        @enforce_provenance()
        async def fake_generator():
            return "Sign here today to claim your free roof under our storm relief program."

        with pytest.raises(SalesPromiseViolationError) as exc_info:
            await fake_generator()

        assert any("free roof" in v.lower() for v in exc_info.value.violations)

    @pytest.mark.asyncio
    async def test_compliant_text_returns_provenance_string(self):
        @enforce_provenance(prompt_name="SALES_SUMMARY_SYSTEM_PROMPT")
        async def fake_generator(job_id: str):
            return "Recent hail activity was recorded in your area. A complimentary inspection can assess your roof."

        result = await fake_generator(job_id="JOB-999")

        assert isinstance(result, ProvenanceString)
        assert result.guardrail_passed is True
        assert "verify_prohibited_sales_promises" in result.guardrail_checks
        assert "job:JOB-999" in result.source_refs
        assert result.prompt_version_hash == get_prompt_version_hash("SALES_SUMMARY_SYSTEM_PROMPT")
        assert "Recent hail activity" in result.text
        assert str(result) == result.text

    @pytest.mark.asyncio
    async def test_gemini_client_generate_text_is_decorated(self):
        """Verify GeminiClient.generate_text blocks prohibited language via decorator."""
        from unittest.mock import MagicMock
        client = GeminiClient()
        mock_response = MagicMock()
        mock_response.text = "We will cover your deductible and make sure insurance pays."
        mock_response.usage_metadata = MagicMock(total_token_count=10)

        with patch.object(client, "_call_with_backoff", return_value=mock_response):
            with pytest.raises(SalesPromiseViolationError):
                await client.generate_text(
                    system_prompt="Test",
                    user_prompt="Test",
                )

    @pytest.mark.asyncio
    async def test_gemini_client_generate_text_compliant_returns_provenance(self):
        """Verify GeminiClient.generate_text returns ProvenanceString on compliant text."""
        from unittest.mock import MagicMock
        client = GeminiClient()
        mock_response = MagicMock()
        mock_response.text = "Visible wind creases on architectural shingles along north eave."
        mock_response.usage_metadata = MagicMock(total_token_count=10)

        with patch.object(client, "_call_with_backoff", return_value=mock_response):
            res = await client.generate_text(
                system_prompt=SALES_SUMMARY_SYSTEM_PROMPT,
                user_prompt="Explain damage",
            )
            assert isinstance(res, ProvenanceString)
            assert res.guardrail_passed is True
            assert res.prompt_version_hash == get_prompt_version_hash(SALES_SUMMARY_SYSTEM_PROMPT)
            assert "Visible wind creases" in res.text

    @pytest.mark.asyncio
    async def test_gemini_transport_client_generate_text_decorated(self):
        """Verify GeminiTransportClient.generate_text also blocks prohibited language."""
        from unittest.mock import MagicMock
        from app.services.ai.client import GeminiTransportClient
        client = GeminiTransportClient()
        mock_response = MagicMock()
        mock_response.text = "We promise a free roof with zero deductible."
        mock_response.usage_metadata = MagicMock(total_token_count=10)

        with patch.object(client, "generate_content", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(SalesPromiseViolationError):
                await client.generate_text(
                    system_prompt="Test",
                    user_prompt="Test",
                )


class TestCachePromptVersionIsolation:
    """Test that modifying a prompt invalidates cache hits for identical images."""

    def test_prompt_change_causes_cache_miss(self):
        init_db()
        job_id = "JOB-ISOLATION-001"
        photo_sha = "sha256_mock_abc123"

        analysis = PhotoAnalysis(
            filename="roof.jpg",
            damage_detected=True,
            damage_type=DamageType.WIND,
            severity=Severity.MODERATE,
            confidence=0.88,
            hail_hits_visible=False,
            crease_marks=True,
            granule_loss=False,
            exposed_fiberglass=False,
            forensic_narrative="Wind uplift observed.",
        )

        prompt_v1 = "Analyze roof photos for hail and wind damage v1"
        prompt_v2 = "Analyze roof photos for hail and wind damage v2 — upgraded criteria"

        hash_v1 = compute_prompt_hash(prompt_v1)
        hash_v2 = compute_prompt_hash(prompt_v2)
        assert hash_v1 != hash_v2

        # 1. Set cache with prompt v1
        set_cached_analysis(job_id, photo_sha, analysis, prompt_hash=hash_v1)

        # 2. Query with prompt v1 -> Cache HIT
        hit = get_cached_analysis(job_id, photo_sha, prompt_hash=hash_v1)
        assert hit is not None
        assert hit.damage_type == DamageType.WIND

        # 3. Query with prompt v2 -> Cache MISS (requires fresh AI run)
        miss = get_cached_analysis(job_id, photo_sha, prompt_hash=hash_v2)
        assert miss is None, "Expected cache miss when prompt version hash differs!"


class TestBuildingCodeCitationGuardrail:
    """Verify invented building code citations are rejected loudly."""

    def test_invented_code_citation_raises_error(self):
        from app.services.ai.guardrails import (
            CodeCitationViolationError,
            verify_building_code_citations,
        )

        bad_text = "The contractor must replace the decking per IRC Section R999.9.9 and local ordinances."
        with pytest.raises(CodeCitationViolationError) as exc_info:
            verify_building_code_citations(bad_text, raise_on_failure=True)
        assert any("R999.9.9" in v for v in exc_info.value.violations)

    def test_valid_code_citation_passes(self):
        from app.services.ai.guardrails import verify_building_code_citations

        good_text = "Drip edge is required along eaves and rakes per IRC Section R905.2.8.5."
        res = verify_building_code_citations(good_text, raise_on_failure=True)
        assert res.passed is True
        assert len(res.violations) == 0

    @pytest.mark.asyncio
    async def test_supplement_narrative_with_invented_code_is_blocked(self):
        """AI generating a narrative with invented code citation is blocked by decorator."""
        from app.services.ai.guardrails import CodeCitationViolationError
        from app.services.ai.provenance import enforce_provenance

        @enforce_provenance(checks=["verify_building_code_citations"])
        async def fake_supplement():
            return "Carrier must pay under IRC Section R111.222.333 for emergency tarping."

        with pytest.raises(CodeCitationViolationError):
            await fake_supplement()


class TestManualReconciliationFlag:
    """Verify mismatched Statement of Loss sets requires_manual_reconciliation and surfaces in triage."""

    @pytest.fixture(autouse=True)
    def use_real_writeback(self, monkeypatch):
        # Override conftest's patch_pipeline_writebacks to run real writeback logic
        import importlib
        import app.core.pipeline
        importlib.reload(app.core.pipeline)

    def test_mismatched_sol_sets_flag_and_surfaces_in_triage(self):
        import sqlite3
        from decimal import Decimal
        from app.core.ingestion_models import (
            ClaimFinancials,
            ClaimLineItem,
            EvidenceRef,
            RoofGeometry,
            SourcedValue,
            UniversalClaimAST,
        )
        from app.core.pipeline import _writeback_sol_financials
        from app.services.next_best_action import evaluate_job_next_actions

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row

        conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                homeowner_name TEXT,
                address_line1 TEXT,
                city TEXT,
                postal_code TEXT,
                phone TEXT,
                status TEXT,
                job_type TEXT,
                last_work_date TEXT,
                canvasser_rep_id TEXT,
                canvasser_name TEXT,
                supplement_sent_at TEXT,
                carrier_sla_days INTEGER,
                requires_manual_reconciliation INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE financials (
                job_id TEXT PRIMARY KEY,
                overhead_pct REAL DEFAULT 0.10,
                canvasser_commission_pct REAL DEFAULT 0.10,
                carrier_rcv_cents INTEGER,
                carrier_initial_rcv_cents INTEGER,
                depreciation_cents INTEGER,
                deductible_cents INTEGER,
                net_claim_cents INTEGER
            )
        """)

        job_id = "JOB-RECON-TEST-001"
        conn.execute(
            "INSERT INTO jobs (id, homeowner_name, status, requires_manual_reconciliation) VALUES (?, 'John Doe', 'LEAD_CAPTURED', 0)",
            (job_id,)
        )
        conn.commit()

        # Build AST where line items ($1,000) mismatch header gross_rcv ($2,000)
        ev_ref = EvidenceRef(doc_id="doc1", page=1, raw_text="raw", extraction_method="test")
        item = ClaimLineItem(
            category_code="RFG",
            activity_code="&",
            description="Shingles",
            quantity=SourcedValue(value=Decimal("10.00"), evidence=[ev_ref]),
            unit=SourcedValue(value="SQ", evidence=[ev_ref]),
            unit_price=SourcedValue(value=Decimal("100.00"), evidence=[ev_ref]),
            tax=SourcedValue(value=Decimal("0.00"), evidence=[ev_ref]),
            claimed_rcv=SourcedValue(value=Decimal("1000.00"), evidence=[ev_ref]),
            depreciation=SourcedValue(value=Decimal("0.00"), evidence=[ev_ref]),
            acv=SourcedValue(value=Decimal("1000.00"), evidence=[ev_ref]),
        )
        ast = UniversalClaimAST(
            line_items=[item],
            roof_geometry=RoofGeometry(
                pitch=SourcedValue(value="6/12"),
                total_squares=SourcedValue(value=Decimal("10.00")),
                eaves_lf=SourcedValue(value=Decimal("100.00")),
                valleys_lf=SourcedValue(value=Decimal("20.00")),
                rakes_lf=SourcedValue(value=Decimal("50.00")),
            ),
            financials=ClaimFinancials(
                gross_rcv=SourcedValue(value=Decimal("2000.00")),  # Mismatch! Expected 1000.00
                total_depreciation=SourcedValue(value=Decimal("500.00")),
                deductible=SourcedValue(value=Decimal("1000.00")),
                net_claim=SourcedValue(value=Decimal("500.00")),
            ),
            source_doc_sha256="fake_sha",
            source_doc_id="doc_id_1",
        )

        # 1. Assert AST model validator detected mismatch
        assert ast.financials.gross_rcv.verified is False
        assert ast.requires_manual_reconciliation is True

        # 2. Writeback updates DB record
        import app.core.pipeline
        app.core.pipeline._writeback_sol_financials(conn, job_id, ast)
        conn.commit()

        # 3. Assert jobs table has requires_manual_reconciliation = 1
        row = conn.execute("SELECT requires_manual_reconciliation FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["requires_manual_reconciliation"] == 1

        # 4. Assert surfaces as Priority 1 action in Next Best Actions
        job_row = dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
        actions = evaluate_job_next_actions(job_row, role="admin")
        recon_actions = [a for a in actions if a["action_type"] == "RECONCILIATION"]
        assert len(recon_actions) == 1
        assert recon_actions[0]["priority"] == 1
        assert recon_actions[0]["urgency"] == "critical"
        assert "Mandatory Statement of Loss Reconciliation" in recon_actions[0]["title"]

