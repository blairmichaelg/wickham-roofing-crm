"""
System Routes: Health check and root redirect
"""

from __future__ import annotations

import subprocess

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.core.database import get_connection

router = APIRouter()
logger = structlog.get_logger("app.api.system_routes")


@router.get("/health", tags=["system"])
async def health_check(request: Request):
    """
    Basic health check endpoint.

    Returns service status. Used by Render for health monitoring
    and to prevent premature instance spin-down.
    """
    settings = get_settings()

    db_ok = True
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception as e:
        logger.error("health_check_db_error", error=str(e))
        db_ok = False

    redis_ok = True
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if redis_pool:
        try:
            await redis_pool.ping()
        except Exception as e:
            logger.error("health_check_redis_error", error=str(e))
            redis_ok = False
    else:
        logger.warning("health_check_redis_missing")
        redis_ok = False

    if not db_ok or not redis_ok:
        logger.warning(
            "health_check_degraded", db_ok=db_ok, redis_ok=redis_ok
        )
        raise HTTPException(status_code=503, detail="Service degraded")

    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        commit_hash = "unknown"

    logger.info("health_check_ok")
    return {
        "status": "ok",
        "env": settings.app_env,
        "db": "ok",
        "redis": "ok",
        "db_path": settings.get_db_path,
        "commit_hash": commit_hash,
    }


@router.get("/", tags=["frontend"], include_in_schema=False)
async def root_redirect():
    """Redirect bare domain to login page."""
    return RedirectResponse(url="/login", status_code=303)
