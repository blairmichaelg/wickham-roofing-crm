"""
Unit Tests for Local Offline Voice-to-Text Transcription & Field Route (Sprint 4).

Tests:
1. Service layer: transcribe_audio_file with mocked WhisperModel and fallback handling.
2. Endpoint layer: POST /api/field/jobs/{job_id}/voice-note validation, doc vault registration, and notes appending.
"""

import io
import uuid
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.core.database import create_field_rep, get_field_rep_by_pin, get_connection
from app.server import app
from app.services.voice_transcription import transcribe_audio_file, get_whisper_model


client = TestClient(app)

if not get_field_rep_by_pin("4444"):
    try:
        create_field_rep("Voice Test Rep", "4444")
    except Exception:
        pass
login_resp = client.post("/auth/login", data={"pin": "4444", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = login_resp.cookies.get("auth_token")
if auth_cookie:
    client.cookies.set("auth_token", auth_cookie)


def test_transcribe_audio_nonexistent_file():
    result = transcribe_audio_file("nonexistent_audio_path_12345.wav")
    assert result == ""


def test_transcribe_audio_with_mocked_model(tmp_path):
    dummy_wav = tmp_path / "test_note.wav"
    dummy_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00")

    mock_segment = MagicMock()
    mock_segment.text = "Hail damage on south slope, missing two ridge caps."
    mock_info = MagicMock()
    mock_info.duration = 4.2
    mock_info.language = "en"

    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = ([mock_segment], mock_info)

    with patch("app.services.voice_transcription.get_whisper_model", return_value=mock_whisper):
        text = transcribe_audio_file(dummy_wav)
        assert "Hail damage on south slope" in text
        assert "missing two ridge caps" in text


def test_transcribe_audio_fallback_when_model_fails(tmp_path):
    dummy_wav = tmp_path / "corrupt.wav"
    dummy_wav.write_bytes(b"bad audio bytes")

    mock_whisper = MagicMock()
    mock_whisper.transcribe.side_effect = RuntimeError("Audio decoding failed")

    with patch("app.services.voice_transcription.get_whisper_model", return_value=mock_whisper):
        text = transcribe_audio_file(dummy_wav)
        assert "[Audio recorded: transcription failed:" in text


def test_field_voice_note_endpoint_success():
    job_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, canvasser_rep_id) 
            VALUES (?, 'Audio Homeowner', '456 Voice Lane', 'Valdosta', 'GA', '31601', '229-555-4321', 'LEAD_CAPTURED', 'rep-michael')""",
            (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    mock_segment = MagicMock()
    mock_segment.text = "Drip edge is rusted along the front eave. Homeowner requested dimensional shingles."
    mock_info = MagicMock()
    mock_info.duration = 6.5

    mock_whisper = MagicMock()
    mock_whisper.transcribe.return_value = ([mock_segment], mock_info)

    audio_bytes = b"fake audio content for upload"

    with patch("app.services.voice_transcription.get_whisper_model", return_value=mock_whisper), \
         patch("app.api.field_routes.assert_field_rep_owns_job", return_value=True):
        
        response = client.post(
            f"/api/field/jobs/{job_id}/voice-note",
            headers={"x-internal-token": auth_cookie} if auth_cookie else {},
            files={"file": ("memo.webm", io.BytesIO(audio_bytes), "audio/webm")}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["job_id"] == job_id
        assert "Drip edge is rusted" in data["transcription"]

    # Verify notes updated in database
    conn = get_connection()
    try:
        cur = conn.execute("SELECT inspection_notes FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        assert row is not None
        assert "Drip edge is rusted" in row["inspection_notes"]

        # Verify document registered in job_documents
        cur = conn.execute("SELECT * FROM job_documents WHERE job_id = ? AND category = 'VOICE_NOTE'", (job_id,))
        doc = cur.fetchone()
        assert doc is not None
        assert doc["visibility"] == "field_safe"
    finally:
        conn.close()


def test_field_voice_note_endpoint_invalid_uuid():
    response = client.post(
        "/api/field/jobs/invalid-uuid-12345/voice-note",
        headers={"x-internal-token": auth_cookie} if auth_cookie else {},
        files={"file": ("memo.wav", io.BytesIO(b"content"), "audio/wav")}
    )
    assert response.status_code == 400
    assert "Invalid job_id format" in response.json()["detail"]


def test_field_voice_note_endpoint_empty_file():
    job_id = str(uuid.uuid4())
    with patch("app.api.field_routes.assert_field_rep_owns_job", return_value=True):
        response = client.post(
            f"/api/field/jobs/{job_id}/voice-note",
            headers={"x-internal-token": auth_cookie} if auth_cookie else {},
            files={"file": ("empty.wav", io.BytesIO(b""), "audio/wav")}
        )
        assert response.status_code == 400
        assert "Empty audio file" in response.json()["detail"]


def test_field_voice_note_endpoint_invalid_format():
    job_id = str(uuid.uuid4())
    with patch("app.api.field_routes.assert_field_rep_owns_job", return_value=True):
        response = client.post(
            f"/api/field/jobs/{job_id}/voice-note",
            headers={"x-internal-token": auth_cookie} if auth_cookie else {},
            files={"file": ("document.exe", io.BytesIO(b"MZ\x90\x00"), "application/x-msdownload")}
        )
        assert response.status_code == 400
        assert "Invalid audio format" in response.json()["detail"]
