"""
Unit tests for the V4 SQLite Database interactions.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.database import JobStatus, update_job_status


def test_update_job_status_valid_enum():
    """Test that a valid enum string passes validation."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        # Mock cursor for SELECT
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"status": "INSTALL_COMPLETED", "status_history": json.dumps([])}
        
        # Should not raise ValueError
        update_job_status("job_123", "INVOICED", "Test note")
        
        # Verify the UPDATE was called
        assert mock_conn.execute.call_count == 5
        assert any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)

def test_update_job_status_atomic_json_append():
    """Test that update_job_status uses the SQLite json_insert atomic append."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        # Mock cursor for SELECT
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"status": "INSTALL_COMPLETED", "status_history": json.dumps([])}
        
        # We need rowcount = 1 for the UPDATE statement
        mock_cursor.rowcount = 1
        
        update_job_status("job_123", "INVOICED", "Test atomic note")
        
        # Verify the UPDATE was called
        assert mock_conn.execute.call_count == 5
        update_call = mock_conn.execute.call_args_list[2]
        
        sql = update_call[0][0]
        params = update_call[0][1]
        
        assert "json_insert(" in sql
        assert "COALESCE(status_history, '[]')" in sql
        assert "'$[#]'" in sql
        
        assert params[0] == "INVOICED"
        assert params[1] == "INVOICED"
        assert params[3] == "Test atomic note"
        assert params[4] == "job_123"

        assert any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)

def test_update_job_status_invalid_string_raises_value_error():
    """Test that arbitrary strings raise a ValueError to prevent bad data."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        with pytest.raises(ValueError, match="Invalid job status: INVALID_STATUS"):
            update_job_status("job_123", "INVALID_STATUS", "This should fail")
            
        # Verify connection was never opened due to early validation
        mock_get_conn.assert_not_called()

def test_update_job_status_missing_job_raises_value_error():
    """Test that a missing job raises a ValueError."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        # Mock cursor for SELECT returning None
        mock_cursor = MagicMock()
        mock_conn.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None
        
        with pytest.raises(ValueError, match="Job job_missing not found."):
            update_job_status("job_missing", "INVOICED", "Test note")


def test_state_machine_material_ordered_missing_financials():
    """Cannot transition to MATERIAL_ORDERED if financials do not exist."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        # 1st execute: SELECT status, status_history from jobs
        mock_cursor_job = MagicMock()
        mock_cursor_job.fetchone.return_value = {"status": "LEAD_CAPTURED", "status_history": json.dumps([])}
        
        # 2nd execute: SELECT revenue FROM financials
        mock_cursor_fin = MagicMock()
        mock_cursor_fin.fetchone.return_value = None
        
        mock_conn.execute.side_effect = [MagicMock(), mock_cursor_job, mock_cursor_fin, MagicMock()]
        
        with pytest.raises(RuntimeError, match="ILLEGAL TRANSITION: Cannot order materials without calculated financials."):
            update_job_status("job_123", JobStatus.MATERIAL_ORDERED)
            
        # Verify transaction was aborted (commit not called)
        assert not any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)


def test_state_machine_invoiced_invalid_prior_state():
    """Cannot transition to INVOICED directly from LEAD_CAPTURED."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        mock_cursor_job = MagicMock()
        mock_cursor_job.fetchone.return_value = {"status": "LEAD_CAPTURED", "status_history": json.dumps([])}
        mock_conn.execute.side_effect = [MagicMock(), mock_cursor_job, MagicMock()]
        
        with pytest.raises(RuntimeError, match="ILLEGAL TRANSITION: Cannot invoice from state LEAD_CAPTURED."):
            update_job_status("job_123", JobStatus.INVOICED)
            
        assert not any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)


def test_state_machine_closed_without_payment():
    """Cannot transition to CLOSED unless PAYMENT_RECEIVED."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        
        mock_cursor_job = MagicMock()
        mock_cursor_job.fetchone.return_value = {"status": "INVOICED", "status_history": json.dumps([])}
        mock_conn.execute.side_effect = [MagicMock(), mock_cursor_job, MagicMock()]
        
        with pytest.raises(RuntimeError, match="ILLEGAL TRANSITION: Cannot close job before PAYMENT_RECEIVED."):
            update_job_status("job_123", JobStatus.CLOSED)
            
        assert not any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)

def test_ai_token_ledger():
    """Test that AI token usage is logged synchronously via SQLite."""
    with patch("app.core.database.get_connection") as mock_get_conn:
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn

        from app.core.database import log_ai_usage
        log_ai_usage("job-777", 1250, "gemini-2.5-flash", "test_op")

        # Verify execute was called with correct SQL
        assert mock_conn.execute.call_count == 3
        args = mock_conn.execute.call_args_list[1][0]
        assert "INSERT INTO ai_usage_logs" in args[0]
        
        # Verify the payload matches (log_id, job_id, tokens, model, op)
        payload = args[1]
        assert payload[1] == "job-777"
        assert payload[2] == 1250
        assert payload[3] == "gemini-2.5-flash"
        assert payload[4] == "test_op"

        assert any('COMMIT' in str(c) for c in mock_conn.execute.call_args_list)
        mock_conn.close.assert_called_once()
