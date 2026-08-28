"""
Unit Tests for Chain-of-Thought Damage Classification Prompt Refinement (Sprint 5).

Tests:
1. PhotoAnalysis schema supports intermediate reasoning fields:
   - granule_depletion_pattern
   - substrate_condition
   - impact_bruise_present
2. Backward compatibility: deserialization succeeds with legacy payloads without CoT fields.
3. Gemini vision prompt includes 3-step Chain-of-Thought observation directives.
"""

import json
from unittest.mock import MagicMock, patch
import pytest

from app.core.inspection_models import DamageType, PhotoAnalysis, Severity
from app.services.ai_service import GeminiClient


def test_photo_analysis_schema_with_cot_fields():
    analysis = PhotoAnalysis(
        filename="test_shingle.jpg",
        damage_detected=True,
        damage_type=DamageType.HAIL,
        severity=Severity.MODERATE,
        confidence_score=95.0,
        granule_depletion_pattern="localized_circular_impact",
        substrate_condition="exposed_fiberglass",
        impact_bruise_present=True,
        hail_hits_visible=True,
        granule_loss=True,
        exposed_fiberglass=True,
        forensic_narrative="Direct hail impact fractured surface granules and exposed underlying fiberglass substrate."
    )

    assert analysis.granule_depletion_pattern == "localized_circular_impact"
    assert analysis.substrate_condition == "exposed_fiberglass"
    assert analysis.impact_bruise_present is True
    assert analysis.confidence == 0.95


def test_photo_analysis_backward_compatibility():
    legacy_json = json.dumps({
        "filename": "legacy_photo.jpg",
        "damage_detected": False,
        "damage_type": "none",
        "severity": "none",
        "confidence_score": 100.0,
        "hail_hits_visible": False,
        "crease_marks": False,
        "granule_loss": False,
        "exposed_fiberglass": False,
        "forensic_narrative": "Roof slope in sound condition with no storm damage."
    })

    analysis = PhotoAnalysis.model_validate_json(legacy_json)
    assert analysis.filename == "legacy_photo.jpg"
    assert analysis.granule_depletion_pattern is None
    assert analysis.substrate_condition is None
    assert analysis.impact_bruise_present is False


@pytest.mark.asyncio
@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
async def test_ai_service_prompt_contains_cot_steps(mock_client_class, mock_get_settings, tmp_path):
    settings = MagicMock()
    settings.gemini_api_key = "fake_api_key"
    mock_get_settings.return_value = settings

    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    dummy_img = tmp_path / "roof_slope.jpg"
    dummy_img.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xFF\xDB\x00C\x00")

    service = GeminiClient()

    captured_prompt = None

    def fake_generate_content(model, contents, config):
        nonlocal captured_prompt
        # contents[1] is the prompt string in inline mode
        captured_prompt = contents[1]
        mock_response = MagicMock()
        mock_response.parsed = PhotoAnalysis(
            filename="roof_slope.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.MODERATE,
            confidence_score=90.0,
            granule_depletion_pattern="localized_circular_impact",
            substrate_condition="intact",
            impact_bruise_present=True,
            hail_hits_visible=True,
            crease_marks=False,
            granule_loss=True,
            exposed_fiberglass=False,
            forensic_narrative="Hail impacts observed with localized granule loss."
        )
        mock_response.usage_metadata = MagicMock(total_token_count=150)
        return mock_response

    mock_client.models.generate_content.side_effect = fake_generate_content

    result = await service.analyze_roof_photo(dummy_img, original_filename="roof_slope.jpg")

    assert captured_prompt is not None
    assert "Step 1 (Granule Depletion Pattern)" in captured_prompt
    assert "Step 2 (Asphalt Substrate / Mat Condition)" in captured_prompt
    assert "Step 3 (Impact Bruise Presence)" in captured_prompt
    assert "MANDATORY CHAIN-OF-THOUGHT OBSERVATION SEQUENCE" in captured_prompt

    assert result.granule_depletion_pattern == "localized_circular_impact"
    assert result.impact_bruise_present is True
