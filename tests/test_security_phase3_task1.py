from unittest.mock import patch

import pytest

from app.workers.supplement_processor import process_supplement_event


@pytest.mark.asyncio
async def test_supplement_processor_role_forbidden():
    """Task 1: Test that an unauthorized role is rejected by the ARQ boundary."""
    result = await process_supplement_event(
        ctx={},
        job_id="dummy",
        role="field"
    )
    assert result["status"] == "forbidden"
    assert result["reason"] == "role_not_allowed_for_supplement"

@pytest.mark.asyncio
@patch('app.workers.supplement_processor.run_supplement_pipeline')
async def test_supplement_processor_role_allowed(mock_pipeline):
    """Task 1: Test that an authorized role proceeds normally."""
    mock_pipeline.return_value = {"status": "success", "message": "mocked"}
    
    result = await process_supplement_event(
        ctx={},
        job_id="dummy",
        role="admin"
    )
    
    assert result["status"] == "success"
    mock_pipeline.assert_called_once()
