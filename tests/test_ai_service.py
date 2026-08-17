"""
Unit tests for the AI Service.

Tests cover the google-genai SDK integration, Pydantic schema validation,
and error handling for the Gemini AI cognitive engine.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.ai_service import get_ai_client


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.gemini_api_key = "fake_api_key"
    return settings


@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_analyze_job_data_success(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test successful JSON parsing from Gemini API."""
    mock_get_settings.return_value = mock_settings

    # Mock the Gemini client and response
    mock_client_instance = MagicMock()
    mock_response = MagicMock()

    expected_decision = {
        "action": "generate_document",
        "reasoning": "Sufficient details provided.",
        "document_data": {
            "materials": ["Shingles", "Underlayment"],
            "total_cost": 5000.0,
        },
    }
    mock_response.text = json.dumps(expected_decision)
    mock_response.usage_metadata.total_token_count = 100
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    payload = {"id": "123", "notes": "Need roof replacement"}

    result = asyncio.run(service.analyze_job_data(payload))

    assert result["action"] == "generate_document"
    assert "materials" in result["document_data"]
    assert mock_client_instance.models.generate_content.called


@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_analyze_job_data_schema_validation_error(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test handling of invalid schema returned by the model."""
    mock_get_settings.return_value = mock_settings

    mock_client_instance = MagicMock()
    mock_response = MagicMock()

    # Simulate a model returning JSON that fails Pydantic validation
    # "unknown_action" is not in the Literal, and total_cost is a string
    mock_response.text = json.dumps({
        "action": "unknown_action",
        "reasoning": "I made this up",
        "document_data": {
            "materials": ["Nails"],
            "total_cost": "A lot"
        }
    })
    mock_response.usage_metadata.total_token_count = 100
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    payload = {"id": "123"}

    result = asyncio.run(service.analyze_job_data(payload))

    # Should gracefully fail and return an error action
    assert result["action"] == "error"
    assert "validation" in result["reasoning"].lower() or "Validation" in result["reasoning"]


@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_extract_sol_from_pdf_success(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test successful multimodal extraction of SoL PDF."""
    mock_get_settings.return_value = mock_settings

    mock_client_instance = MagicMock()
    
    # Mock upload
    mock_file = MagicMock()
    mock_file.name = "files/12345"
    mock_client_instance.files.upload.return_value = mock_file
    
    # Mock file state (ACTIVE immediately)
    mock_file_info = MagicMock()
    mock_file_info.state.name = "ACTIVE"
    mock_client_instance.files.get.return_value = mock_file_info
    
    # Mock generation response (for classify_carrier AND extract_sol)
    # The first call will be to classify_carrier, which returns a string response.
    # The second call will be to extract_sol, which returns the parsed StatementOfLoss.
    # Wait, classify_carrier is a method on AIService, we can just mock it directly!
    
    # Mock generation response
    mock_response = MagicMock()
    from app.core.supplement_models import LineItem, StatementOfLoss
    mock_response.parsed = StatementOfLoss(
        carrier_name="State Farm",
        claim_number="1234",
        line_items=[
            LineItem(trade="Roof", code="RFG", description="Shingles", quantity=20.0, unit_of_measure="SQ", unit_price=100.0)
        ],
        overhead_and_profit_included=True
    )
    mock_response.usage_metadata.total_token_count = 100
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    with patch.object(service, "classify_carrier", return_value="xactimate"):
        result = asyncio.run(service.extract_sol_from_pdf("fake.pdf"))

    assert result.carrier_name == "State Farm"
    assert result.source_system == "xactimate"
    assert result.claim_number == "1234"
    assert len(result.line_items) == 1
    assert result.line_items[0].quantity == 20.0
    
    # Verify cleanup was called
    mock_client_instance.files.delete.assert_called_once_with(name="files/12345")


@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_extract_sol_from_pdf_processing_failure(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test handling of file processing failure on Gemini servers."""
    mock_get_settings.return_value = mock_settings

    mock_client_instance = MagicMock()
    
    mock_file = MagicMock()
    mock_file.name = "files/badfile"
    mock_client_instance.files.upload.return_value = mock_file
    
    # Mock file state returning FAILED
    mock_file_info = MagicMock()
    mock_file_info.state.name = "FAILED"
    mock_client_instance.files.get.return_value = mock_file_info
    
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    with pytest.raises(RuntimeError, match="File processing failed"):
        asyncio.run(service.extract_sol_from_pdf("fake.pdf"))

@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_extract_sol_symbility_routing(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test successful multimodal extraction of SoL PDF when routed to Symbility."""
    mock_get_settings.return_value = mock_settings

    mock_client_instance = MagicMock()
    
    # Mock upload
    mock_file = MagicMock()
    mock_file.name = "files/12345"
    mock_client_instance.files.upload.return_value = mock_file
    
    # Mock file state (ACTIVE immediately)
    mock_file_info = MagicMock()
    mock_file_info.state.name = "ACTIVE"
    mock_client_instance.files.get.return_value = mock_file_info
    
    # Mock generation response
    mock_response = MagicMock()
    from app.core.supplement_models import LineItem, StatementOfLoss
    mock_response.parsed = StatementOfLoss(
        carrier_name="Allstate",
        claim_number="5678",
        line_items=[
            LineItem(trade="Roof", code="RFG", description="Shingles", quantity=20.0, unit_of_measure="SQ", unit_price=100.0, waste_percent_included=0.10)
        ],
        overhead_and_profit_included=True
    )
    mock_response.usage_metadata.total_token_count = 100
    mock_client_instance.models.generate_content.return_value = mock_response
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    with patch.object(service, "classify_carrier", return_value="symbility"):
        result = asyncio.run(service.extract_sol_from_pdf("fake.pdf"))

    assert result.carrier_name == "Allstate"
    assert result.source_system == "symbility"
    assert result.line_items[0].waste_percent_included == 0.10
    
    # Ensure classify was called (implicitly covered if the code reaches here and mock was used)
    
    # Verify cleanup was called
    mock_client_instance.files.delete.assert_called_once_with(name="files/12345")


@patch("app.services.ai_service.get_settings")
@patch("app.services.ai_service.genai.Client")
def test_extract_sol_from_pdf_finally_block_on_error(
    mock_client_class, mock_get_settings, mock_settings
):
    """Test that the remote file is cleaned up even if the LLM throws an exception."""
    mock_get_settings.return_value = mock_settings

    mock_client_instance = MagicMock()
    
    # Mock upload
    mock_file = MagicMock()
    mock_file.name = "files/leak_test"
    mock_client_instance.files.upload.return_value = mock_file
    
    # Mock file state (ACTIVE)
    mock_file_info = MagicMock()
    mock_file_info.state.name = "ACTIVE"
    mock_client_instance.files.get.return_value = mock_file_info
    
    # Mock generation response throwing an error
    mock_client_instance.models.generate_content.side_effect = Exception("Google API 500 Error")
    mock_client_class.return_value = mock_client_instance

    service = get_ai_client()
    with patch.object(service, "classify_carrier", return_value="xactimate"):
        with pytest.raises(Exception, match="Google API 500 Error"):
            asyncio.run(service.extract_sol_from_pdf("fake.pdf"))

    # VERIFY: The finally block must execute the cleanup!
    mock_client_instance.files.delete.assert_called_once_with(name="files/leak_test")

def test_photo_processor_abstraction():
    """Verify that photo_processor.py does not import google.genai directly."""
    import ast
    with open("app/workers/photo_processor.py") as f:
        tree = ast.parse(f.read())
        
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "google.genai" not in alias.name, "Found direct google.genai import!"
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "google.genai" not in node.module, "Found direct google.genai import!"
        elif isinstance(node, ast.Attribute):
            # Also ensure ai.client is not accessed
            if getattr(node, "attr", "") == "client":
                if isinstance(node.value, ast.Name) and getattr(node.value, "id", "") == "ai":
                    pytest.fail("Found direct ai.client access!")
