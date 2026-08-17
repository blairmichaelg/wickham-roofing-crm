"""
Unit tests for the V3 Inspection Engine Pydantic models and Drive sync guard.

Tests the flat PhotoAnalysis schema, InspectionJob computed properties,
InspectionPhoto extension validation, and the get_stable_photos() dual-guard
(mtime staleness + SHA256 deduplication).
"""

import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.core.inspection_models import (
    DamageType,
    InspectionJob,
    InspectionPhoto,
    PhotoAnalysis,
    Severity,
    _compute_sha256,
    get_stable_photos,
)

# ── PhotoAnalysis Schema Tests ────────────────────────────────────────────────


class TestPhotoAnalysis:
    """Tests for the flat Gemini structured output schema."""

    def test_full_damage_analysis(self):
        """Verify all forensic flags and narrative populate correctly."""
        analysis = PhotoAnalysis(
            filename="IMG_0042.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.SEVERE,
            confidence=0.92,
            hail_hits_visible=True,
            crease_marks=False,
            granule_loss=True,
            exposed_fiberglass=True,
            forensic_narrative=(
                "Multiple circular impact marks consistent with hail damage visible "
                "on the north-facing slope. Significant granule loss exposes fiberglass mat."
            ),
        )
        assert analysis.damage_detected is True
        assert analysis.damage_type == DamageType.HAIL
        assert analysis.severity == Severity.SEVERE
        assert analysis.hail_hits_visible is True
        assert analysis.exposed_fiberglass is True
        assert analysis.crease_marks is False
        assert 0.0 <= analysis.confidence <= 1.0

    def test_no_damage_analysis(self):
        """Verify clean roof produces all-false flags."""
        analysis = PhotoAnalysis(
            filename="IMG_0001.jpg",
            damage_detected=False,
            damage_type=DamageType.NONE,
            severity=Severity.NONE,
            confidence=0.98,
            forensic_narrative="No visible storm damage. Shingles are intact with normal wear.",
        )
        assert analysis.damage_detected is False
        assert analysis.hail_hits_visible is False
        assert analysis.granule_loss is False
        assert analysis.exposed_fiberglass is False
        assert analysis.crease_marks is False

    def test_wind_damage_with_crease_marks(self):
        """Verify wind-specific flags."""
        analysis = PhotoAnalysis(
            filename="IMG_0099.png",
            damage_detected=True,
            damage_type=DamageType.WIND,
            severity=Severity.MODERATE,
            confidence=0.85,
            crease_marks=True,
            granule_loss=True,
            forensic_narrative="Crease lines visible on lifted shingle tabs. Granule displacement at fold points.",
        )
        assert analysis.damage_type == DamageType.WIND
        assert analysis.crease_marks is True

    def test_confidence_boundary_low(self):
        """Confidence must accept 0.0."""
        analysis = PhotoAnalysis(
            filename="dark_photo.jpg",
            damage_detected=False,
            damage_type=DamageType.NONE,
            severity=Severity.NONE,
            confidence=0.0,
            forensic_narrative="Image too dark for reliable analysis.",
        )
        assert analysis.confidence == 0.0

    def test_confidence_boundary_high(self):
        """Confidence must accept 1.0."""
        analysis = PhotoAnalysis(
            filename="clear_photo.jpg",
            damage_detected=True,
            damage_type=DamageType.HAIL,
            severity=Severity.SEVERE,
            confidence=1.0,
            forensic_narrative="Textbook hail damage pattern.",
        )
        assert analysis.confidence == 1.0

    def test_confidence_out_of_range_rejects(self):
        """Confidence > 1.0 must raise ValidationError."""
        with pytest.raises(Exception):
            PhotoAnalysis(
                filename="bad.jpg",
                damage_detected=True,
                damage_type=DamageType.HAIL,
                severity=Severity.SEVERE,
                confidence=1.5,
                forensic_narrative="Invalid.",
            )


# ── InspectionPhoto Tests ─────────────────────────────────────────────────────


class TestInspectionPhoto:
    """Tests for the photo metadata model and extension validation."""

    def test_valid_jpeg(self):
        photo = InspectionPhoto(filepath=Path("photos/IMG_001.jpg"))
        assert photo.filepath.suffix == ".jpg"

    def test_valid_png(self):
        photo = InspectionPhoto(filepath=Path("photos/IMG_002.png"))
        assert photo.filepath.suffix == ".png"

    def test_valid_heic(self):
        """iPhone photos are typically HEIC."""
        photo = InspectionPhoto(filepath=Path("photos/IMG_003.HEIC"))
        assert photo.filepath.suffix == ".HEIC"

    def test_invalid_extension_rejects(self):
        """Non-image files must be rejected at parse time."""
        with pytest.raises(Exception):
            InspectionPhoto(filepath=Path("notes.pdf"))

    def test_invalid_extension_txt(self):
        with pytest.raises(Exception):
            InspectionPhoto(filepath=Path("readme.txt"))

    def test_sha256_stored(self):
        photo = InspectionPhoto(
            filepath=Path("photos/IMG_004.jpg"),
            sha256="abc123def456",
        )
        assert photo.sha256 == "abc123def456"

    def test_captured_at_optional(self):
        photo = InspectionPhoto(filepath=Path("photos/IMG_005.webp"))
        assert photo.captured_at is None


# ── InspectionJob Tests ───────────────────────────────────────────────────────


class TestInspectionJob:
    """Tests for the batch container and computed properties."""

    def _make_analysis(self, damaged: bool, severity: Severity) -> PhotoAnalysis:
        return PhotoAnalysis(
            filename="test.jpg",
            damage_detected=damaged,
            damage_type=DamageType.HAIL if damaged else DamageType.NONE,
            severity=severity,
            confidence=0.9,
            forensic_narrative="Test narrative.",
        )

    def test_empty_job(self):
        job = InspectionJob(
            job_id="WR-001",
            property_address="123 Main St, Valdosta, GA",
            inspection_date=datetime(2026, 6, 30),
        )
        assert job.total_photos == 0
        assert job.damage_count == 0
        assert job.has_actionable_damage is False

    def test_damage_count(self):
        job = InspectionJob(
            job_id="WR-002",
            property_address="456 Oak Dr, Moultrie, GA",
            inspection_date=datetime(2026, 6, 30),
            analyses=[
                self._make_analysis(True, Severity.SEVERE),
                self._make_analysis(True, Severity.MINOR),
                self._make_analysis(False, Severity.NONE),
            ],
        )
        assert job.damage_count == 2

    def test_actionable_damage_true(self):
        """Moderate or severe damage should trigger claim filing."""
        job = InspectionJob(
            job_id="WR-003",
            property_address="789 Pine Ln, Ochlocknee, GA",
            inspection_date=datetime(2026, 6, 30),
            analyses=[
                self._make_analysis(True, Severity.MODERATE),
            ],
        )
        assert job.has_actionable_damage is True

    def test_actionable_damage_false_minor_only(self):
        """Minor-only damage should NOT trigger claim filing."""
        job = InspectionJob(
            job_id="WR-004",
            property_address="321 Elm St, Valdosta, GA",
            inspection_date=datetime(2026, 6, 30),
            analyses=[
                self._make_analysis(True, Severity.MINOR),
                self._make_analysis(True, Severity.MINOR),
            ],
        )
        assert job.has_actionable_damage is False

    def test_default_inspector(self):
        job = InspectionJob(
            job_id="WR-005",
            property_address="100 Test Ave",
            inspection_date=datetime(2026, 6, 30),
        )
        assert job.inspector_name == "Wickham Roofing LLC"


# ── Drive Sync Guard Tests ────────────────────────────────────────────────────


class TestGetStablePhotos:
    """Tests for the Google Drive sync guard and SHA256 deduplication."""

    def test_stable_files_ingested(self, tmp_path):
        """Files older than settle_seconds should be returned."""
        img = tmp_path / "photo1.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        # Backdate mtime by 30 seconds so it passes the 10-second guard
        old_time = time.time() - 30
        os.utime(img, (old_time, old_time))

        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 1
        assert result[0].filepath == img
        assert result[0].sha256 is not None

    def test_settling_files_skipped(self, tmp_path):
        """Files modified within settle_seconds should be skipped."""
        img = tmp_path / "syncing.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        # mtime is NOW — file is still settling
        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 0

    def test_non_image_files_skipped(self, tmp_path):
        """Non-image extensions should be silently skipped."""
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("some notes")
        old_time = time.time() - 30
        os.utime(txt_file, (old_time, old_time))

        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 0

    def test_duplicate_hashes_skipped(self, tmp_path):
        """Files with previously seen SHA256 hashes should be skipped."""
        img = tmp_path / "photo_dup.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
        old_time = time.time() - 30
        os.utime(img, (old_time, old_time))

        # Pre-compute the hash and pass it as already-processed
        existing_hash = _compute_sha256(img)
        processed = {existing_hash}

        result = get_stable_photos(tmp_path, settle_seconds=10, processed_hashes=processed)
        assert len(result) == 0

    def test_multiple_stable_files(self, tmp_path):
        """Multiple valid photos should all be returned in sorted order."""
        for name in ["alpha.jpg", "beta.png", "gamma.webp"]:
            f = tmp_path / name
            # Write unique content so hashes differ
            f.write_bytes(name.encode() + b"\x00" * 50)
            old_time = time.time() - 30
            os.utime(f, (old_time, old_time))

        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 3
        # Verify sorted order
        names = [p.filepath.name for p in result]
        assert names == ["alpha.jpg", "beta.png", "gamma.webp"]

    def test_hidden_files_skipped(self, tmp_path):
        """Google Drive temp files (dotfiles) should be skipped."""
        hidden = tmp_path / ".gd_sync_tmp.jpg"
        hidden.write_bytes(b"\x00" * 50)
        old_time = time.time() - 30
        os.utime(hidden, (old_time, old_time))

        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 0

    def test_nonexistent_directory(self, tmp_path):
        """Non-existent directory should return empty list, not crash."""
        fake_dir = tmp_path / "does_not_exist"
        result = get_stable_photos(fake_dir, settle_seconds=10)
        assert result == []

    def test_captured_at_from_mtime(self, tmp_path):
        """captured_at should be populated from the file's mtime."""
        img = tmp_path / "timed.jpg"
        img.write_bytes(b"\xff\xd8" + b"\x00" * 50)
        target_time = time.time() - 60
        os.utime(img, (target_time, target_time))

        result = get_stable_photos(tmp_path, settle_seconds=10)
        assert len(result) == 1
        assert result[0].captured_at is not None
        assert isinstance(result[0].captured_at, datetime)

    def test_hash_set_is_mutated(self, tmp_path):
        """processed_hashes set should be updated with new file hashes."""
        img = tmp_path / "new_photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        old_time = time.time() - 30
        os.utime(img, (old_time, old_time))

        tracking_set: set[str] = set()
        result = get_stable_photos(tmp_path, settle_seconds=10, processed_hashes=tracking_set)
        assert len(result) == 1
        assert len(tracking_set) == 1
        assert result[0].sha256 in tracking_set
