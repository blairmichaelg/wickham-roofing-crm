"""
SQLite-backed caching layer for the V3 Inspection Pipeline.

Provides thread-safe persistence for Gemini 2.5 Flash PhotoAnalysis results.
By keying off the job_id and the image's SHA256 hash, we ensure that if
a job is re-run (e.g. due to crash or field user adding photos later),
we do not burn duplicate API tokens for images already processed.
"""

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import structlog

from app.core.inspection_models import PhotoAnalysis

logger = structlog.get_logger("app.core.cache")

DB_PATH = Path("data/cache.db")


def compute_prompt_hash(prompt_text: str) -> str:
    """Compute stable 16-char hex hash of a prompt string."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


@contextmanager
def _get_connection() -> Iterator[sqlite3.Connection]:
    """Provide a WAL-mode, crash-safe transactional connection to the cache DB."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    """Initialize the SQLite cache schema and ensure composite primary key includes prompt_hash."""
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(analysis_cache)")
        existing_cols = {row[1]: row for row in cursor.fetchall()}

        if not existing_cols:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_cache (
                    job_id TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL DEFAULT 'legacy',
                    analysis_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (job_id, sha256, prompt_hash)
                )
                """
            )
        else:
            # Check if prompt_hash is part of the primary key
            has_prompt_hash_col = "prompt_hash" in existing_cols
            is_pk = existing_cols["prompt_hash"][5] > 0 if has_prompt_hash_col else False

            if not is_pk:
                logger.info("migrating_analysis_cache_pk_with_prompt_hash")
                cursor.execute("ALTER TABLE analysis_cache RENAME TO analysis_cache_old")
                cursor.execute(
                    """
                    CREATE TABLE analysis_cache (
                        job_id TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        prompt_hash TEXT NOT NULL DEFAULT 'legacy',
                        analysis_json TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (job_id, sha256, prompt_hash)
                    )
                    """
                )
                if has_prompt_hash_col:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO analysis_cache (job_id, sha256, prompt_hash, analysis_json, created_at)
                        SELECT job_id, sha256, prompt_hash, analysis_json, created_at FROM analysis_cache_old
                        """
                    )
                else:
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO analysis_cache (job_id, sha256, prompt_hash, analysis_json, created_at)
                        SELECT job_id, sha256, 'legacy', analysis_json, created_at FROM analysis_cache_old
                        """
                    )
                cursor.execute("DROP TABLE analysis_cache_old")

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_cache_lookup ON analysis_cache (job_id, sha256, prompt_hash)"
        )
        conn.commit()
    logger.info("cache_db_initialized", db_path=str(DB_PATH))


def get_cached_analysis(job_id: str, sha256: str, prompt_hash: str | None = None) -> PhotoAnalysis | None:
    """
    Retrieve a cached PhotoAnalysis.

    Args:
        job_id: The job identifier.
        sha256: The hash of the uploaded photo.
        prompt_hash: Optional hash of the active prompt template. If provided,
                     only returns results generated with this exact prompt version.

    Returns:
        PhotoAnalysis if found, else None.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        if prompt_hash is not None:
            cursor.execute(
                "SELECT analysis_json FROM analysis_cache WHERE job_id = ? AND sha256 = ? AND prompt_hash = ?",
                (job_id, sha256, prompt_hash),
            )
        else:
            cursor.execute(
                "SELECT analysis_json FROM analysis_cache WHERE job_id = ? AND sha256 = ? ORDER BY created_at DESC LIMIT 1",
                (job_id, sha256),
            )
        row = cursor.fetchone()

        if row:
            logger.debug("cache_hit", job_id=job_id, sha256=sha256[:12], prompt_hash=prompt_hash)
            try:
                return PhotoAnalysis.model_validate_json(row[0])
            except Exception as e:
                logger.error("cache_deserialization_failed", job_id=job_id, sha256=sha256[:12], error=str(e))
                return None

    logger.debug("cache_miss", job_id=job_id, sha256=sha256[:12], prompt_hash=prompt_hash)
    return None


def set_cached_analysis(
    job_id: str,
    sha256: str,
    analysis: PhotoAnalysis,
    prompt_hash: str = "legacy",
) -> None:
    """
    Store a PhotoAnalysis result in the cache with prompt version isolation.

    Args:
        job_id: The job identifier.
        sha256: The hash of the uploaded photo.
        analysis: The parsed PhotoAnalysis to persist.
        prompt_hash: Stable version hash of the prompt used to generate this analysis.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO analysis_cache (job_id, sha256, prompt_hash, analysis_json)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, sha256, prompt_hash, analysis.model_dump_json()),
        )
        conn.commit()
    logger.debug("cache_set", job_id=job_id, sha256=sha256[:12], prompt_hash=prompt_hash)


def get_cached_analyses_for_job(job_id: str, prompt_hash: str | None = None) -> list[PhotoAnalysis]:
    """
    Retrieve all cached PhotoAnalyses for a specific job.
    Used by the field API to build job summaries without reprocessing.
    """
    with _get_connection() as conn:
        cursor = conn.cursor()
        if prompt_hash is not None:
            cursor.execute(
                "SELECT analysis_json FROM analysis_cache WHERE job_id = ? AND prompt_hash = ?",
                (job_id, prompt_hash),
            )
        else:
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
