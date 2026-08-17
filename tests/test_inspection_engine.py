"""
Unit tests for the V3 Vision Engine: backoff logic, inspection processor
file lifecycle, image resizer, and temp file manager.

Tests use unittest.mock to simulate Gemini API responses without
making real API calls. The backoff tests use monkeypatching to
eliminate actual sleep delays.
"""

import asyncio
import io
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image as PILImage

from app.core.inspection_models import (
    DamageType,
    InspectionJob,
    InspectionPhoto,
    PhotoAnalysis,
    Severity,
)
from app.core.temp_manager import (
    _reset_tracking,
    cleanup_all,
    create_temp_file,
    get_tracked_count,
    track_file,
)
from app.workers.inspection_processor import resize_for_ai, resize_for_pdf

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.gemini_api_key = "fake_api_key"
    return settings


@pytest.fixture
def sample_analysis():
    """A reusable PhotoAnalysis for mocking Gemini responses."""
    return PhotoAnalysis(
        filename="test.jpg",
        damage_detected=True,
        damage_type=DamageType.HAIL,
        severity=Severity.SEVERE,
        confidence=0.92,
        hail_hits_visible=True,
        crease_marks=False,
        granule_loss=True,
        exposed_fiberglass=False,
        forensic_narrative="Multiple circular impact marks consistent with hail.",
    )


def _create_test_image(path: Path, width: int = 100, height: int = 80) -> None:
    """Create a real image file on disk for Pillow tests."""
    img = PILImage.new("RGB", (width, height), color=(128, 128, 128))
    img.save(str(path), format="JPEG")


# ── Backoff Logic Tests ───────────────────────────────────────────────────────


class TestCallWithBackoff:
    """Tests for AIService._call_with_backoff rate-limit retry logic."""

    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_success_on_first_try(self, mock_client_class, mock_get_settings, mock_settings):
        """No retries needed when the call succeeds immediately."""
        mock_get_settings.return_value = mock_settings
        mock_client_class.return_value = MagicMock()

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_func = MagicMock(return_value="success")
        result = service._call_with_backoff(mock_func)

        assert result == "success"
        assert mock_func.call_count == 1

    @patch("app.services.ai_service.time.sleep")
    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_retries_on_429_and_503(self, mock_client_class, mock_get_settings, mock_sleep, mock_settings):
        """Should retry on 429 and 503 and succeed on the 3rd attempt."""
        mock_get_settings.return_value = mock_settings
        mock_client_class.return_value = MagicMock()

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_func = MagicMock(
            side_effect=[
                Exception("429 RESOURCE_EXHAUSTED"),
                Exception("503 Service Unavailable"),
                "success_after_retries",
            ]
        )

        result = service._call_with_backoff(mock_func, max_retries=5)
        assert result == "success_after_retries"
        assert mock_func.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("app.services.ai_service.time.sleep")
    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_max_retries_exhausted(self, mock_client_class, mock_get_settings, mock_sleep, mock_settings):
        """Should raise RuntimeError after exhausting all retries."""
        mock_get_settings.return_value = mock_settings
        mock_client_class.return_value = MagicMock()

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_func = MagicMock(side_effect=Exception("429 RESOURCE_EXHAUSTED"))

        with pytest.raises(RuntimeError, match="transient error exceeded after 3 retries"):
            service._call_with_backoff(mock_func, max_retries=3)

        assert mock_func.call_count == 3

    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_non_rate_limit_error_raises_immediately(self, mock_client_class, mock_get_settings, mock_settings):
        """Non-429 errors should be re-raised without retrying."""
        mock_get_settings.return_value = mock_settings
        mock_client_class.return_value = MagicMock()

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_func = MagicMock(side_effect=ValueError("Something else broke"))

        with pytest.raises(ValueError, match="Something else broke"):
            service._call_with_backoff(mock_func)

        assert mock_func.call_count == 1

    @patch("app.services.ai_service.time.sleep")
    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_backoff_sleep_increases_exponentially(self, mock_client_class, mock_get_settings, mock_sleep, mock_settings):
        """Verify sleep durations follow 2^attempt + jitter pattern."""
        mock_get_settings.return_value = mock_settings
        mock_client_class.return_value = MagicMock()

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_func = MagicMock(side_effect=Exception("429 RESOURCE_EXHAUSTED"))

        with pytest.raises(RuntimeError):
            service._call_with_backoff(mock_func, max_retries=3)

        # 3 retries = 3 sleeps. Check each sleep arg is >= 2^attempt
        assert mock_sleep.call_count == 3
        for i, sleep_call in enumerate(mock_sleep.call_args_list):
            actual_wait = sleep_call[0][0]
            min_expected = 2 ** i  # 1, 2, 4
            assert actual_wait >= min_expected, (
                f"Attempt {i}: sleep({actual_wait}) < minimum {min_expected}"
            )


# ── analyze_roof_photo Tests ─────────────────────────────────────────────────


class TestAnalyzeRoofPhoto:
    """Tests for AIService.analyze_roof_photo structured output."""

    @patch("app.services.ai_service.get_settings")
    @patch("app.services.ai_service.genai.Client")
    def test_returns_photo_analysis(self, mock_client_class, mock_get_settings, mock_settings, sample_analysis):
        """Should return a validated PhotoAnalysis from Gemini's structured output."""
        mock_get_settings.return_value = mock_settings

        mock_client_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = sample_analysis
        mock_response.usage_metadata.total_token_count = 100
        mock_client_instance.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client_instance

        from app.services.ai_service import GeminiClient
        service = GeminiClient()

        mock_file_info = MagicMock()
        result = asyncio.run(service.analyze_roof_photo(mock_file_info, "test.jpg"))

        assert isinstance(result, PhotoAnalysis)
        assert result.damage_detected is True
        assert result.damage_type == DamageType.HAIL
        assert result.hail_hits_visible is True

        # Verify the call used response_schema=PhotoAnalysis
        call_kwargs = mock_client_instance.models.generate_content.call_args
        config = call_kwargs.kwargs["config"]
        assert config.response_schema == PhotoAnalysis
        assert config.response_mime_type == "application/json"


# ── Inspection Processor Lifecycle Tests ──────────────────────────────────────


class TestInspectionProcessor:
    """Tests for the sequential photo processing lifecycle."""

    @patch("app.workers.inspection_processor.set_cached_analysis")
    @patch("app.workers.inspection_processor.get_cached_analysis")
    @patch("app.workers.inspection_processor.asyncio.sleep")
    @patch("app.workers.inspection_processor.get_ai_client")
    def test_full_lifecycle(self, mock_ai_class, mock_sleep, mock_get_cache, mock_set_cache, tmp_path, sample_analysis):
        """Verify get_cache -> analyze_roof_photos_batch -> set_cache lifecycle for each photo."""
        mock_get_cache.return_value = None
        
        # Create a real image file
        img_path = tmp_path / "photo1.jpg"
        _create_test_image(img_path)

        # Mock AIService instance
        mock_ai = MagicMock()
        mock_ai_class.return_value = mock_ai

        # Mock analysis
        sample_analysis.filename = "photo1.jpg"
        mock_ai.analyze_roof_photos_batch = AsyncMock(return_value=[sample_analysis])

        job = InspectionJob(
            job_id="WR-TEST-001",
            property_address="123 Test St, Valdosta, GA",
            inspection_date=datetime(2026, 6, 30),
            photos=[InspectionPhoto(filepath=img_path, sha256="fake_hash")],
        )

        from app.workers.inspection_processor import process_inspection
        with patch("app.workers.inspection_processor.get_inspection_summary", new_callable=AsyncMock) as mock_get_summary:
            mock_get_summary.return_value = job
            result = asyncio.run(process_inspection({"is_test": True}, "WR-TEST-001"))

        # Verify lifecycle
        mock_get_cache.assert_called_once_with("WR-TEST-001", "fake_hash")
        mock_ai.analyze_roof_photos_batch.assert_called_once()
        mock_set_cache.assert_called_once()

        assert len(result.analyses) == 1
        assert result.analyses[0].damage_detected is True

    @patch("app.workers.inspection_processor.set_cached_analysis")
    @patch("app.workers.inspection_processor.get_cached_analysis")
    @patch("app.workers.inspection_processor.asyncio.sleep")
    @patch("app.workers.inspection_processor.get_ai_client")
    def test_failed_processing_skips_photo(self, mock_ai_class, mock_sleep, mock_get_cache, mock_set_cache, tmp_path):
        """Photos that fail batch analysis or return empty results should be skipped, not crash."""
        mock_get_cache.return_value = None
        
        img_path = tmp_path / "bad_photo.jpg"
        _create_test_image(img_path)

        mock_ai = MagicMock()
        mock_ai_class.return_value = mock_ai
        mock_ai.analyze_roof_photos_batch = AsyncMock(return_value=[])

        job = InspectionJob(
            job_id="WR-TEST-002",
            property_address="456 Fail Rd",
            inspection_date=datetime(2026, 6, 30),
            photos=[InspectionPhoto(filepath=img_path, sha256="fake_hash")],
        )

        from app.workers.inspection_processor import process_inspection
        with patch("app.workers.inspection_processor.get_inspection_summary", new_callable=AsyncMock) as mock_get_summary:
            mock_get_summary.return_value = job
            result = asyncio.run(process_inspection({"is_test": True}, "WR-TEST-002"))

        assert len(result.analyses) == 0
        mock_set_cache.assert_not_called()

    @patch("app.workers.inspection_processor.set_cached_analysis")
    @patch("app.workers.inspection_processor.get_cached_analysis")
    @patch("app.workers.inspection_processor.asyncio.sleep")
    @patch("app.workers.inspection_processor.get_ai_client")
    def test_multiple_photos_batch(self, mock_ai_class, mock_sleep, mock_get_cache, mock_set_cache, tmp_path, sample_analysis):
        """Multiple photos should be processed in batch."""
        mock_get_cache.return_value = None
        
        photos = []
        for i in range(3):
            p = tmp_path / f"photo_{i}.jpg"
            _create_test_image(p)
            photos.append(InspectionPhoto(filepath=p, sha256=f"hash_{i}"))

        mock_ai = MagicMock()
        mock_ai_class.return_value = mock_ai

        # Mock batch analysis
        analysis0 = sample_analysis.model_copy(update={"filename": "photo_0.jpg"})
        analysis1 = sample_analysis.model_copy(update={"filename": "photo_1.jpg"})
        analysis2 = sample_analysis.model_copy(update={"filename": "photo_2.jpg"})
        mock_ai.analyze_roof_photos_batch = AsyncMock(return_value=[analysis0, analysis1, analysis2])

        job = InspectionJob(
            job_id="WR-TEST-003",
            property_address="789 Sequential Ln",
            inspection_date=datetime(2026, 6, 30),
            photos=photos,
        )

        from app.workers.inspection_processor import process_inspection
        with patch("app.workers.inspection_processor.get_inspection_summary", new_callable=AsyncMock) as mock_get_summary:
            mock_get_summary.return_value = job
            result = asyncio.run(process_inspection({"is_test": True}, "WR-TEST-003"))

        assert len(result.analyses) == 3
        assert mock_set_cache.call_count == 3
        mock_ai.analyze_roof_photos_batch.assert_called_once()

    @patch("app.workers.inspection_processor.set_cached_analysis")
    @patch("app.workers.inspection_processor.get_cached_analysis")
    @patch("app.workers.inspection_processor.asyncio.sleep")
    @patch("app.workers.inspection_processor.get_ai_client")
    def test_cleanup_runs_on_analysis_error(self, mock_ai_class, mock_sleep, mock_get_cache, mock_set_cache, tmp_path):
        """Processor should survive analysis throwing an exception and complete successfully."""
        mock_get_cache.return_value = None
        
        img_path = tmp_path / "error_photo.jpg"
        _create_test_image(img_path)

        mock_ai = MagicMock()
        mock_ai_class.return_value = mock_ai

        # Mock batch to raise error
        mock_ai.analyze_roof_photos_batch = AsyncMock(side_effect=RuntimeError("Rate limit exhausted"))

        job = InspectionJob(
            job_id="WR-TEST-004",
            property_address="101 Error Ave",
            inspection_date=datetime(2026, 6, 30),
            photos=[InspectionPhoto(filepath=img_path, sha256="fake_hash")],
        )

        from app.workers.inspection_processor import process_inspection
        with patch("app.workers.inspection_processor.get_inspection_summary", new_callable=AsyncMock) as mock_get_summary:
            mock_get_summary.return_value = job
            result = asyncio.run(process_inspection({"is_test": True}, "WR-TEST-004"))

        assert len(result.analyses) == 0
        mock_set_cache.assert_not_called()

    @patch("app.workers.inspection_processor.get_cached_analysis")
    @patch("app.workers.inspection_processor.get_ai_client")
    def test_cache_hit_bypasses_ai(self, mock_ai_class, mock_get_cache, tmp_path, sample_analysis):
        """A cache hit should instantly append the analysis and skip Gemini completely."""
        mock_get_cache.return_value = sample_analysis
        
        img_path = tmp_path / "cached_photo.jpg"
        _create_test_image(img_path)
        
        mock_ai = MagicMock()
        mock_ai_class.return_value = mock_ai
        
        job = InspectionJob(
            job_id="WR-TEST-005",
            property_address="Cache St",
            inspection_date=datetime(2026, 6, 30),
            photos=[InspectionPhoto(filepath=img_path, sha256="hash_hit")],
        )
        
        from app.workers.inspection_processor import process_inspection
        with patch("app.workers.inspection_processor.get_inspection_summary", new_callable=AsyncMock) as mock_get_summary:
            mock_get_summary.return_value = job
            result = asyncio.run(process_inspection({"is_test": True}, "WR-TEST-005"))
        
        assert len(result.analyses) == 1
        assert result.analyses[0] == sample_analysis
        
        # Verify API was bypassed entirely
        mock_ai.analyze_roof_photos_batch.assert_not_called()


# ── Image Resizer Tests ───────────────────────────────────────────────────────


class TestResizeForPdf:
    """Tests for the Pillow-based image downsampler."""

    def test_large_image_downsampled(self, tmp_path):
        """Images wider than max_width should be resized proportionally."""
        img_path = tmp_path / "large.jpg"
        _create_test_image(img_path, width=4000, height=3000)

        result_buf = resize_for_pdf(img_path, max_width=800)

        result_img = PILImage.open(result_buf)
        assert result_img.width == 800
        assert result_img.height == 600  # Proportional: 3000 * (800/4000)

    def test_small_image_unchanged(self, tmp_path):
        """Images already under max_width should not be resized."""
        img_path = tmp_path / "small.jpg"
        _create_test_image(img_path, width=400, height=300)

        result_buf = resize_for_pdf(img_path, max_width=800)

        result_img = PILImage.open(result_buf)
        assert result_img.width == 400
        assert result_img.height == 300

    def test_output_is_png(self, tmp_path):
        """Output buffer should always be PNG format."""
        img_path = tmp_path / "test.jpg"
        _create_test_image(img_path, width=200, height=150)

        result_buf = resize_for_pdf(img_path, max_width=800)

        result_img = PILImage.open(result_buf)
        assert result_img.format == "PNG"

    def test_returns_seeked_bytesio(self, tmp_path):
        """Returned BytesIO should be at position 0, ready for ImageReader."""
        img_path = tmp_path / "seeked.jpg"
        _create_test_image(img_path, width=500, height=400)

        result_buf = resize_for_pdf(img_path, max_width=800)

        assert isinstance(result_buf, io.BytesIO)
        assert result_buf.tell() == 0

    def test_exact_boundary_width(self, tmp_path):
        """Image at exactly max_width should not be resized."""
        img_path = tmp_path / "exact.jpg"
        _create_test_image(img_path, width=800, height=600)

        result_buf = resize_for_pdf(img_path, max_width=800)

        result_img = PILImage.open(result_buf)
        assert result_img.width == 800


class TestResizeForAi:
    """Tests for the Pillow-based image downsampler to 1600px max width."""
    
    def setup_method(self):
        _reset_tracking()
    
    def test_large_image_downsampled_to_file(self, tmp_path):
        """Images wider than max_width should be resized and saved as temporary JPEGs."""
        img_path = tmp_path / "large_raw.jpg"
        _create_test_image(img_path, width=4000, height=3000)

        result_path = resize_for_ai(img_path, max_width=1600)

        assert Path(result_path).exists()
        
        result_img = PILImage.open(result_path)
        assert result_img.width == 1600
        assert result_img.height == 1200  # Proportional: 3000 * (1600/4000)
        assert result_img.format == "JPEG"
        
        # Verify temp_manager is tracking it
        assert get_tracked_count() == 1
        
        # Cleanup
        cleanup_all()


# ── Temp Manager Tests ────────────────────────────────────────────────────────


class TestTempManager:
    """Tests for the centralized temp file manager."""

    def setup_method(self):
        """Reset tracking state before each test."""
        _reset_tracking()

    def test_create_temp_file(self):
        """Should create a real file and track it."""
        path = create_temp_file(suffix=".pdf")
        assert Path(path).exists()
        assert get_tracked_count() == 1
        # Cleanup
        Path(path).unlink()

    def test_cleanup_deletes_files(self, tmp_path):
        """cleanup_all should delete all tracked files."""
        f1 = tmp_path / "temp1.pdf"
        f2 = tmp_path / "temp2.pdf"
        f1.write_bytes(b"fake pdf")
        f2.write_bytes(b"fake pdf 2")

        track_file(f1)
        track_file(f2)
        assert get_tracked_count() == 2

        cleanup_all()

        assert not f1.exists()
        assert not f2.exists()
        assert get_tracked_count() == 0

    def test_cleanup_survives_missing_file(self, tmp_path):
        """cleanup_all should not crash if a tracked file is already deleted."""
        f = tmp_path / "already_gone.pdf"
        f.write_bytes(b"data")
        track_file(f)
        f.unlink()  # Already deleted

        # Should not raise
        cleanup_all()
        assert get_tracked_count() == 0

    def test_track_file_adds_to_registry(self, tmp_path):
        """track_file should add an external file to the cleanup registry."""
        f = tmp_path / "external.pdf"
        f.write_bytes(b"data")
        track_file(f)
        assert get_tracked_count() == 1
