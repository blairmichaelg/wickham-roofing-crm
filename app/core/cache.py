"""
SQLite-backed caching layer for the V3 Inspection Pipeline.

Provides thread-safe persistence for Gemini 2.5 Flash PhotoAnalysis results.
By keying off the job_id and the image's SHA256 hash, we ensure that if
a job is re-run (e.g. due to crash or field user adding photos later),
we do not burn duplicate API tokens for images already processed.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import structlog

from app.core.inspection_models import PhotoAnalysis

logger = structlog.get_logger("app.core.cache")

DB_PATH = Path("data/cache.db")


@contextmanager
def _get_connection():
    """Provide a WAL-mode, crash-safe transactional connection to the cache DB."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=67108864")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the SQLite cache schema."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_cache (
                job_id TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, sha256)
            )
            """
        )
        conn.commit()
    logger.info("cache_db_initialized", db_path=str(DB_PATH))


def get_cached_analysis(job_id: str, sha256: str) -> PhotoAnalysis | None:
    """
    Retrieve a cached PhotoAnalysis.

    Args:
        job_id: The job identifier.
        sha256: The hash of the uploaded photo.

    Returns:
        PhotoAnalysis if found, else None.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT analysis_json FROM analysis_cache WHERE job_id = ? AND sha256 = ?",
            (job_id, sha256),
        )
        row = cursor.fetchone()

        if row:
            logger.debug("cache_hit", job_id=job_id, sha256=sha256[:12])
            try:
                return PhotoAnalysis.model_validate_json(row[0])
            except Exception as e:
                logger.error("cache_deserialization_failed", job_id=job_id, sha256=sha256[:12], error=str(e))
                return None

    logger.debug("cache_miss", job_id=job_id, sha256=sha256[:12])
    return None


def set_cached_analysis(job_id: str, sha256: str, analysis: PhotoAnalysis) -> None:
    """
    Store a PhotoAnalysis result in the cache.

    Args:
        job_id: The job identifier.
        sha256: The hash of the uploaded photo.
        analysis: The parsed PhotoAnalysis to persist.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO analysis_cache (job_id, sha256, analysis_json)
            VALUES (?, ?, ?)
            """,
            (job_id, sha256, analysis.model_dump_json()),
        )
        conn.commit()
    logger.debug("cache_set", job_id=job_id, sha256=sha256[:12])


def get_cached_analyses_for_job(job_id: str) -> list[PhotoAnalysis]:
    """
    Retrieve all cached PhotoAnalyses for a specific job.
    Used by the field API to build job summaries without reprocessing.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT analysis_json FROM analysis_cache WHERE job_id = ?",
            (job_id,),
        )
        rows = cursor.fetchall()

    analyses = []
    for row in rows:
        try:
            analyses.append(PhotoAnalysis.model_validate_json(row[0]))
        except Exception:
            pass  # Ignore malformed cache entries

    return analyses
