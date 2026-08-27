import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from app.services.ai_service import get_ai_client
from app.services.weather_forensics import NOAAForensicsEngine
from app.core.inspection_models import Severity, DamageType, PhotoAnalysis

@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.gemini_api_key = "fake_api_key"
    settings.gemini_model_name = "gemini-2.5-flash"
    return settings

@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_classify_carrier_success(mock_client_class, mock_get_settings, mock_settings):
    """Test classify_carrier correctly classifies standard insurance carriers."""
    mock_get_settings.return_value = mock_settings
    mock_client_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Xactimate"
    mock_response.usage_metadata.total_token_count = 50
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    result = asyncio.run(service.classify_carrier("Carrier text summary", "job123"))
    assert result == "xactimate"

@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_generate_text_success(mock_client_class, mock_get_settings, mock_settings):
    """Test generate_text helper function."""
    mock_get_settings.return_value = mock_settings
    mock_client_instance = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Response text"
    mock_response.usage_metadata.total_token_count = 20
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    result = asyncio.run(service.generate_text("Prompt text", "job123"))
    assert result == "Response text"

@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_analyze_roof_photos_batch_inline(mock_client_class, mock_get_settings, mock_settings):
    """Test batch photo analysis via inline byte payloads."""
    mock_get_settings.return_value = mock_settings
    mock_client_instance = MagicMock()
    
    # Mock batch return structure
    mock_response = MagicMock()
    
    # Mock class with PhotoAnalysis objects
    mock_parsed_result = MagicMock()
    mock_parsed_result.analyses = [
        PhotoAnalysis(
            filename="p1.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.MODERATE,
            confidence=0.9,
            confidence_score=0.9,
            hail_hits_visible=True,
            crease_marks=False,
            granule_loss=True,
            exposed_fiberglass=False,
            forensic_narrative="Factual narrative"
        )
    ]
    mock_response.parsed = mock_parsed_result
    mock_response.usage_metadata.total_token_count = 150
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    
    # Create small dummy file
    dummy_photo_path = Path("tests/dummy_p1.jpg")
    try:
        dummy_photo_path.write_bytes(b"dummy image bytes")
        
        results = asyncio.run(service.analyze_roof_photos_batch([dummy_photo_path], ["p1.jpg"], "job123"))
        assert len(results) == 1
        assert results[0].damage_detected is True
    finally:
        if dummy_photo_path.exists():
            dummy_photo_path.unlink()

def test_noaa_forensics_engine_verify_storm():
    """Test NOAAForensicsEngine static verify_storm function."""
    res = NOAAForensicsEngine.verify_storm(33.749, -84.388, datetime.now())
    assert res["event_type"] == "Hail"
    assert res["match_confidence"] == "HIGH"
    assert res["magnitude"] == 1.75
