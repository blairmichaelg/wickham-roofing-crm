"""
Centralized temporary file manager with atexit cleanup.

Tracks all temporary files created during pipeline execution (PDFs, image
buffers, etc.) and force-deletes them when the Python process terminates.
Prevents local storage bloat from V3's image-heavy pipeline.

Usage:
    from app.core.temp_manager import create_temp_file, track_file

    path = create_temp_file(suffix=".pdf")   # Auto-tracked
    track_file(existing_path)                 # Track an external file
    # All tracked files are cleaned up on process exit via atexit.
"""

import atexit
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger("app.core.temp_manager")

# Module-level registry of files to clean up on exit.
_tracked_files: list[Path] = []


def create_temp_file(suffix: str = ".pdf") -> str:
    """
    Create a secure temporary file and register it for cleanup.

    The file is created with delete=False so ReportLab / Pillow can write to it.
    It is automatically deleted when the process exits via the atexit hook.

    Args:
        suffix: File extension for the temp file (default ".pdf").

    Returns:
        Absolute filepath as a string.
    """
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = Path(f.name)
    f.close()
    _tracked_files.append(path)
    logger.debug("temp_file_created", path=str(path))
    return str(path)


def track_file(filepath: str | Path) -> None:
    """
    Register an existing file for cleanup on process exit.

    Use this when a file is created outside of create_temp_file()
    but still needs to be cleaned up (e.g., Pillow thumbnail buffers
    written to disk).

    Args:
        filepath: Path to the file to track.
    """
    _tracked_files.append(Path(filepath))


def cleanup_all() -> None:
    """
    Delete all tracked temporary files. Called automatically via atexit.

    Failures are logged but never raised — cleanup must not crash the process.
    """
    cleaned = 0
    failed = 0
    for p in _tracked_files:
        try:
            if p.exists():
                p.unlink()
                cleaned += 1
        except OSError as e:
            logger.warning("temp_cleanup_failed", path=str(p), error=str(e))
            failed += 1
    _tracked_files.clear()
    if cleaned or failed:
        logger.info("temp_cleanup_complete", cleaned=cleaned, failed=failed)


def get_tracked_count() -> int:
    """Return the number of currently tracked files. Used for testing."""
    return len(_tracked_files)


def _reset_tracking() -> None:
    """Reset the tracked files list. Used ONLY in tests."""
    _tracked_files.clear()


# Register the cleanup hook. Runs when the Python interpreter exits.
atexit.register(cleanup_all)
