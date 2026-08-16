"""
app/server.py — FastAPI application factory.

Responsibility:
- Create and configure the FastAPI application instance.
- Register all middleware (CORS, NoCacheMiddleware, auth redirect).
- Mount static files and Jinja2 templates.
- Register Jinja2 template filters (status_label, days_since).
- Include all API routers.
- Manage the application lifespan (startup / shutdown resources).

Usage:
    The canonical ``app`` object is created here and re-exported by
    ``app/main.py`` so that ``uvicorn app.main:app`` continues to work
    without changes to any service scripts or Task Scheduler entries.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_jobs_routes import router as admin_jobs_router
from app.api.admin_reps_routes import router as admin_reps_router
from app.api.auth_routes import router as auth_router
from app.api.field_routes import router as field_router
from app.api.frontend_routes import router as frontend_router
from app.api.office_routes import router as office_router
from app.api.operations_routes import router as operations_router
from app.api.system_routes import router as system_router
from app.api.webhooks import router as webhook_router
from app.api.websockets import router as websockets_router
from app.config import get_settings
from app.core.cache import init_db as init_cache_db
from app.core.database import run_migrations as init_crm_db
from app.core.notifications import notifier
from app.core.status_labels import STATUS_LABELS
from app.core.utils import days_since
from app.infra import configure_logging, create_redis_pool

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def redis_pubsub_listener(app: FastAPI):
    """
    Listens for storm alerts on Redis pub/sub and broadcasts them via WebSocket.
    """
    import asyncio
    import json
    import structlog
    from app.core.notifications import notifier
    
    logger = structlog.get_logger("app.redis_pubsub")
    logger.info("redis_pubsub_listener_started")
    
    # Wait until redis_pool is attached to app state
    while not getattr(app.state, "redis_pool", None):
        await asyncio.sleep(0.1)
        
    redis_pool = app.state.redis_pool
    pubsub = redis_pool.pubsub()
    
    try:
        await pubsub.subscribe("channel:storm_alerts")
        logger.info("subscribed_to_storm_alerts_channel")
        
        async for message in pubsub.listen():
            if message and message.get("type") == "message":
                try:
                    data_str = message.get("data")
                    if isinstance(data_str, bytes):
                        data_str = data_str.decode("utf-8")
                    alert_data = json.loads(data_str)
                    logger.info("received_redis_pubsub_alert", alert=alert_data)
                    await notifier.broadcast(alert_data)
                except Exception as e:
                    logger.error("failed_to_broadcast_pubsub_alert", error=str(e))
    except asyncio.CancelledError:
        logger.info("redis_pubsub_listener_cancelled")
    except Exception as e:
        logger.error("redis_pubsub_listener_error", error=str(e))
    finally:
        try:
            await pubsub.unsubscribe("channel:storm_alerts")
            await pubsub.aclose()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown lifecycle.

    Startup:
    - Validate configuration (fail fast on missing env vars)
    - Configure structured logging
    - Initialize shared resources (DB, Redis pool)
    - Create required filesystem directories

    Shutdown:
    - Cleanly close connections
    """
    import asyncio
    logger = structlog.get_logger("app.lifespan")
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "application_starting",
        env=settings.app_env,
        quarantine_status=settings.quarantine_status,
    )

    if settings.app_env.lower() == "prod":
        logger.info("[PROD MODE] Using data/wickham.db on port 8000")
    else:
        logger.info("[DEV MODE] Using data/wickham_dev.db on port 8001")

    # Initialize databases and required directories
    init_cache_db()
    init_crm_db()
    os.makedirs("field_photos", exist_ok=True)
    os.makedirs("data/field_docs", exist_ok=True)
    os.makedirs("signed_agreements", exist_ok=True)
    logger.info("v3_infrastructure_initialized")

    # Attach ARQ Redis pool to app state for use in route handlers
    redis_pool = await create_redis_pool()
    app.state.redis_pool = redis_pool
    logger.info("arq_redis_pool_attached_to_app_state")

    # Start Redis Pub/Sub listener for storm alerts
    app.state.pubsub_listener_task = asyncio.create_task(redis_pubsub_listener(app))
    logger.info("redis_pubsub_listener_task_started")

    logger.info("application_ready")
    yield

    # Shutdown
    logger.info("application_shutting_down")
    if hasattr(app.state, "pubsub_listener_task"):
        app.state.pubsub_listener_task.cancel()
        try:
            await app.state.pubsub_listener_task
        except asyncio.CancelledError:
            pass

    if hasattr(app.state, "redis_pool"):
        await app.state.redis_pool.close()
    logger.info("application_stopped")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and fully configure the FastAPI application."""
    application = FastAPI(
        title="Wickham Roofing AI Orchestrator",
        description="Standalone CRM orchestrator and Google Gemini AI middleware.",
        version="2.2.0",
        lifespan=lifespan,
    )

    # --- CORS ---
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_origin_regex=r"https://.*\.ngrok-free\.app|https://.*\.trycloudflare\.com",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- No-Cache for HTML/JS ---
    class NoCacheMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            response: Response = await call_next(request)
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type or "application/javascript" in content_type:
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response

    application.add_middleware(NoCacheMiddleware)

    # --- Auth redirect middleware ---
    @application.middleware("http")
    async def auth_redirect_middleware(request: Request, call_next):
        """Redirect unauthenticated browser requests to /login.
        API routes (/api/*) always return JSON 401 — no redirect.
        """
        response = await call_next(request)
        if (
            response.status_code == 401
            and not request.url.path.startswith("/api/")
            and not request.url.path.startswith("/auth/")
            and not request.url.path.startswith("/login")
            and not request.url.path.startswith("/health")
            and not request.url.path.startswith("/static/")
            and "text/html" in request.headers.get("accept", "")
        ):
            return RedirectResponse(
                url=f"/login?redirect_url={request.url.path}",
                status_code=303,
            )
        return response

    # --- Static files & templates ---
    os.makedirs("app/static", exist_ok=True)
    os.makedirs("app/templates", exist_ok=True)
    application.mount("/static", StaticFiles(directory="app/static"), name="static")

    templates = Jinja2Templates(directory="app/templates")
    templates.env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s)
    templates.env.filters["days_since"] = days_since

    # Attach templates to app state so route handlers can access them
    application.state.templates = templates

    # --- Routers ---
    application.include_router(webhook_router)
    application.include_router(field_router)
    application.include_router(office_router)
    application.include_router(operations_router)
    application.include_router(auth_router)
    application.include_router(admin_reps_router)
    application.include_router(admin_jobs_router)
    application.include_router(system_router)
    application.include_router(websockets_router)
    application.include_router(frontend_router)

    return application


# Module-level app instance — imported by app/main.py as a re-export.
app: FastAPI = create_app()

# Expose templates at module level for import by app/main.py route handlers.
templates: Jinja2Templates = app.state.templates

# Re-export notifier for WebSocket handler in app/main.py
__all__ = ["app", "templates", "notifier"]
