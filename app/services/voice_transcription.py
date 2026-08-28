"""
Local Offline Voice Transcription Service for Field Notes.

Uses faster-whisper on CPU with int8 quantization for zero-GPU, low-latency,
offline transcription of field voice notes and inspection voice memos.
"""

from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("app.services.voice_transcription")

_whisper_model: Any = None


def get_whisper_model() -> Any:
    """
    Lazy-load and return the cached WhisperModel singleton.
    Defaults to 'tiny' on CPU with int8 quantization.
    """
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped,import-not-found]
            logger.info("loading_whisper_model", model_size="tiny", device="cpu", compute_type="int8")
            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        except Exception as exc:
            logger.warning("whisper_model_initialization_failed", error=str(exc))
            return None
    return _whisper_model


def transcribe_audio_file(audio_path: Path | str) -> str:
    """
    Transcribe a local audio recording (WAV, MP3, WebM, OGG, M4A).
    Returns cleaned transcription text or a graceful fallback message on failure.
    """
    path = Path(audio_path)
    if not path.exists():
        logger.warning("audio_file_not_found", path=str(path))
        return ""

    model = get_whisper_model()
    if model is None:
        logger.warning("whisper_transcription_unavailable", path=str(path))
        return "[Audio recorded: local transcription engine unavailable]"

    try:
        segments, info = model.transcribe(str(path), beam_size=5)
        text_segments = [segment.text.strip() for segment in segments]
        transcription = " ".join(text_segments).strip()
        logger.info(
            "audio_transcription_succeeded",
            path=str(path),
            duration=getattr(info, "duration", 0),
            language=getattr(info, "language", "en"),
            text_length=len(transcription),
        )
        return transcription
    except Exception as exc:
        logger.error("audio_transcription_error", path=str(path), error=str(exc))
        return f"[Audio recorded: transcription failed: {exc}]"
