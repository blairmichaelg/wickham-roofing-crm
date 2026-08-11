"""
FastAPI application entrypoint.

Sets up the application with:
- Lifespan context manager for startup/shutdown resource management
- Structured JSON logging via structlog
- Health check endpoint
- Webhook router mount
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC

import structlog
from arq import create_pool
from fastapi import (
    Depends,
    FastAPI,
    Form,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.admin_jobs_routes import router as admin_jobs_router
from app.api.admin_reps_routes import router as admin_reps_router
from app.api.auth import (
    get_current_role,
    verify_field,
    verify_office_role,
)
from app.api.auth_routes import router as auth_router
from app.api.field_routes import router as field_router
from app.api.office_routes import router as office_router
from app.api.operations_routes import router as operations_router
from app.api.webhooks import router as webhook_router
from app.config import get_settings
from app.core.cache import init_db as init_cache_db
from app.core.database import (
    _fetch_job_sync,
    get_connection,
    get_job_documents,
    list_field_reps,
)
from app.core.database import run_migrations as init_crm_db
from app.core.notifications import notifier


def configure_logging(log_level: str) -> None:
    """Configure structlog for JSON-structured logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            # Use JSON in production, pretty console in development
            (
                structlog.dev.ConsoleRenderer()
                if get_settings().app_env == "development"
                else structlog.processors.JSONRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set root logging level
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.DEBUG),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown lifecycle.

    Startup:
    - Validate configuration (fail fast on missing env vars)
    - Initialize shared resources (httpx client, Redis pool)

    Shutdown:
    - Cleanly close connections
    """
    logger = structlog.get_logger("app.lifespan")

    # --- Startup ---
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "application_starting",
        env=settings.app_env,
        quarantine_status=settings.quarantine_status,
    )
    
    # Stark visibility for Dev/Prod split
    if settings.app_env.lower() == "prod":
        logger.info("[PROD MODE] Using data/wickham.db on port 8000")
    else:
        logger.info("[DEV MODE] Using data/wickham_dev.db on port 8001")

    # Initialize V3 Cache and Directories (Epic 1 & 2)
    init_cache_db()
    # Initialize V4 CRM DB
    init_crm_db()
    os.makedirs("field_photos", exist_ok=True)
    os.makedirs("data/field_docs", exist_ok=True)
    os.makedirs("signed_agreements", exist_ok=True)
    logger.info("v3_infrastructure_initialized")

    # Initialize the ARQ Redis pool for task enqueueing (Phase 3)
    from app.workers.settings import get_redis_settings

    redis_pool = await create_pool(get_redis_settings())
    app.state.redis_pool = redis_pool
    logger.info("arq_redis_pool_attached_to_app_state")

    logger.info("application_ready")

    yield

    # --- Shutdown ---
    logger.info("application_shutting_down")


    # Close the ARQ Redis pool (Phase 3)
    if hasattr(app.state, "redis_pool"):
        await app.state.redis_pool.close()

    logger.info("application_stopped")


app = FastAPI(
    title="Wickham Roofing AI Orchestrator",
    description="Standalone CRM orchestrator and Google Gemini AI middleware.",
    version="1.6.1",
    lifespan=lifespan,
)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    allow_origin_regex=r"https://.*\.ngrok-free\.app|https://.*\.trycloudflare\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type or "application/javascript" in content_type:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheMiddleware)

# --- Static & Templates ---
os.makedirs("app/static", exist_ok=True)
os.makedirs("app/templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

STATUS_LABELS = {
    "LEAD_CAPTURED": "New Lead",
    "CONTINGENCY_SIGNED": "Agreement Signed",
    "RETAIL_CONTRACT_SIGNED": "Retail Contract Signed",
    "CLAIM_FILED": "Claim Filed — Waiting on Adjuster",
    "ADJUSTER_MEETING_COMPLETED": "Adjuster Met — Waiting on Estimate",
    "PHOTOS_UPLOADED": "Photos Uploaded",
    "EV_PARSED": "Measurements Received",
    "MEASUREMENT_PARSED": "Measurements Received",
    "STATEMENT_OF_LOSS_RECEIVED": "Insurance Estimate Received",
    "PENDING_OPERATOR_REVIEW": "Manual Review Required",
    "PIPELINE_FAILED": "Processing Error — Needs Attention",
    "INSPECTION_FAILED": "Inspection Processing Failed",
    "SUPPLEMENT_GENERATED": "Supplement Ready to Send",
    "SUPPLEMENT_SUBMITTED": "Supplement Sent to Carrier",
    "SUPPLEMENT_DENIED": "Supplement Denied — Needs Rebuttal",
    "SUPPLEMENT_APPROVED": "Supplement Approved",
    "SCOPE_APPROVED": "Scope Approved",
    "MATERIAL_ORDERED": "Materials Ordered",
    "MATERIALS_ON_SITE": "Materials On Site",
    "INSTALL_SCHEDULED": "Install Scheduled",
    "INSTALL_COMPLETED": "Install Completed",
    "INSPECTION_COMPLETED": "Initial Inspection Completed",
    "FINAL_INSPECTION": "Final Inspection",
    "FINAL_INSPECTION_COMPLETED": "Final Inspection Completed",
    "INVOICED": "Invoiced",
    "PAYMENT_RECEIVED": "Payment Received",
    "CLOSED": "Job Closed",
    "RETAIL_QUOTE_GENERATED": "Quote Generated",
    "RETAIL_QUOTE_ACCEPTED": "Quote Accepted",
    "RETAIL_QUOTE_DECLINED": "Quote Declined",
    "AWAITING_CARRIER_RESPONSE": "Waiting on Insurance Company",
    "APPRAISAL_INVOKED": "Appraisal Process Started",
    "CLAIM_DENIED": "Claim Denied by Insurer",
}

templates.env.filters["status_label"] = lambda s: STATUS_LABELS.get(s, s)


def days_since(date_str: str) -> int:
    if not date_str:
        return 0
    from datetime import datetime
    try:
        # Handle ISO8601 strings that might contain the 'Z' suffix or '+00:00'
        date_str = date_str.removesuffix('Z')
        if '+' in date_str:
            date_str = date_str.split('+')[0]
            
        # Attempt to parse as full ISO format first, then fallback to SQLite format
        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            
        return (datetime.now(UTC).replace(tzinfo=None) - dt.replace(tzinfo=None)).days
    except Exception:
        return 0

templates.env.filters["days_since"] = days_since

# --- Mount Routers ---
app.include_router(webhook_router)
app.include_router(field_router)
app.include_router(office_router)
app.include_router(operations_router)
app.include_router(auth_router)
app.include_router(admin_reps_router)
app.include_router(admin_jobs_router)

@app.middleware("http")
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



@app.websocket("/ws/office")
async def office_ws(websocket: WebSocket):
    # Token validation before handshake accept
    from app.api.auth import decode_token
    token = websocket.query_params.get("token") or websocket.cookies.get("auth_token")
    if not token:
        await websocket.close(code=1008, reason="Unauthorized: Missing authentication token")
        return
    try:
        payload = decode_token(token)
        if payload.get("role") not in ["admin", "operations", "accounting"]:
            await websocket.close(code=1008, reason="Forbidden: Unauthorized role for office feed")
            return
    except Exception:
        await websocket.close(code=1008, reason="Unauthorized: Invalid token")
        return

    await notifier.connect(websocket, client_id="office_client", role=payload.get("role", "office"))
    try:
        while True:
            data = await websocket.receive_text()
            if data == "pong":
                notifier.update_pong(websocket)
    except WebSocketDisconnect:
        notifier.disconnect(websocket)


# --- Health Check ---
@app.get("/health", tags=["system"])
async def health_check(request: Request):
    """
    Basic health check endpoint.

    Returns service status. Used by Render for health monitoring
    and to prevent premature instance spin-down.
    """
    settings = get_settings()
    
    # 1. DB check
    from app.core.database import get_connection
    db_ok = True
    try:
        conn = get_connection()
        conn.execute("SELECT 1")
        conn.close()
    except Exception as e:
        structlog.get_logger().error("health_check_db_error", error=str(e))
        db_ok = False

    # 2. Redis check
    redis_ok = True
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if redis_pool:
        try:
            # arq Redis pool ping
            await redis_pool.ping()
        except Exception as e:
            structlog.get_logger().error("health_check_redis_error", error=str(e))
            redis_ok = False
    else:
        structlog.get_logger().warning("health_check_redis_missing")
        redis_ok = False

    if not db_ok or not redis_ok:
        from fastapi import HTTPException
        structlog.get_logger().warning("health_check_degraded", db_ok=db_ok, redis_ok=redis_ok)
        raise HTTPException(status_code=503, detail="Service degraded")

    # Get git commit hash for deployment visibility
    import subprocess
    try:
        commit_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], 
            text=True, 
            stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        commit_hash = "unknown"

    structlog.get_logger().info("health_check_ok")
    return {
        "status": "ok",
        "env": settings.app_env,
        "db": "ok",
        "redis": "ok",
        "db_path": settings.get_db_path,
        "commit_hash": commit_hash
    }


# --- Frontend ---
@app.get("/field", tags=["frontend"])
async def serve_field_app(request: Request, role: str = Depends(verify_field)):
    """Serve the Wickham Roofing Field App."""
    reps = await asyncio.to_thread(list_field_reps, False)
    token = request.cookies.get("auth_token", "")
    current_rep_name = None
    claims = {}
    if token:
        try:
            from app.api.auth import decode_token
            claims = decode_token(token)
            current_rep_name = claims.get("rep_name")
        except Exception:
            pass
    from app.api.auth import is_core_user
    is_core = is_core_user(claims)

    return templates.TemplateResponse(request, "field_app.html", {
        "request": request,
        "reps": reps,
        "role": role,
        "is_core": is_core,
        "current_rep_name": current_rep_name,
        "active_page": "field",
        "field_token": token
    })

@app.get("/help", tags=["frontend"])
async def help_page(request: Request, role: str = Depends(get_current_role)):
    return templates.TemplateResponse(
        request, "help.html", {"request": request, "role": role}
    )

@app.get("/", tags=["frontend"], include_in_schema=False)
async def root_redirect():
    """Redirect bare domain to login page."""
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", tags=["frontend"])
async def serve_login(request: Request, redirect_url: str = "/"):
    """Serve the universal login page with optional post-auth redirect target."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "redirect_url": redirect_url},
    )

@app.post("/login", tags=["frontend"])
async def process_login(request: Request, access_code: str = Form(...)):
    """Process login and route to persona dashboard based on PIN."""
    settings = get_settings()
    
    role = None
    redirect_url = "/"
    rep_name: str | None = None
    rep_id: str | None = None
    
    if access_code == settings.admin_pin:
        role = "admin"
        redirect_url = "/admin"
    elif access_code == settings.accounting_pin:
        role = "accounting"
        redirect_url = "/accounting"
    elif access_code == settings.operations_pin:
        role = "operations"
        redirect_url = "/api/operations/board"
    else:
        # Dynamic field rep lookup (Phase 9)
        from app.core.database import get_field_rep_by_pin
        rep = get_field_rep_by_pin(access_code)
        if rep:
            role = "field"
            rep_name = rep["name"]
            rep_id = rep["id"]
            redirect_url = "/field"
        

        
    if role:
        from app.api.auth import create_access_token
        token = create_access_token(
            role,
            rep_name=rep_name if role == "field" else None,
            rep_id=rep_id if role == "field" else None,
        )
        
        response = RedirectResponse(url=redirect_url, status_code=303)
        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=True,
            secure=(settings.app_env == "production"),
            samesite="lax"
        )
        return response
        
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Invalid Access Code"})

def _fetch_active_jobs_sync():
    conn = get_connection()
    try:
        cursor = conn.execute('''
            SELECT id, invoice_id, homeowner_name, address_line1, city, state,
                   status, created_at, canvasser_name, supplement_sent_at, carrier_sla_days
            FROM jobs
            WHERE status != 'CLOSED'
            ORDER BY created_at DESC
        ''')
        return [dict(r) for r in cursor]
    finally:
        conn.close()


@app.get("/office", tags=["frontend"])
async def route_office_dashboard(role: str = Depends(get_current_role)):
    """Route user to their role-appropriate dashboard from generic /office link."""
    if role == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    elif role == "accounting":
        return RedirectResponse(url="/accounting", status_code=303)
    elif role == "operations":
        return RedirectResponse(url="/api/operations/board", status_code=303)
    elif role == "field":
        return RedirectResponse(url="/field", status_code=303)
    return RedirectResponse(url="/admin", status_code=303)

@app.get("/admin", tags=["frontend"])
async def serve_admin_dashboard(request: Request, role: str = Depends(verify_office_role)):
    """Serve the Admin Kanban Board."""
    jobs = await asyncio.to_thread(_fetch_active_jobs_sync)
    return templates.TemplateResponse(request, "admin_dashboard.html", {
        "request": request, 
        "jobs": jobs,
        "active_page": "admin",
        "auth_token": request.cookies.get("auth_token", "")
    })

@app.get("/admin/reps", tags=["frontend"])
async def admin_reps_page(request: Request, role: str = Depends(verify_office_role)):
    """Serve the Field Rep Management page."""
    reps = await asyncio.to_thread(list_field_reps, True)
    return templates.TemplateResponse(
        request,
        "admin_reps.html",
        {
            "reps": reps,
            "role": role,
            "active_page": "admin",
        },
    )

@app.get("/accounting", tags=["frontend"])
async def serve_accounting_dashboard(request: Request, role: str = Depends(verify_office_role)):
    """Serve the Accounting Ledger."""
    return templates.TemplateResponse(request, "accounting_dashboard.html", {
        "request": request, 
        "active_page": "accounting",
        "auth_token": request.cookies.get("auth_token", "")
    })


@app.get("/office/canvassing", tags=["frontend"])
async def serve_canvassing(request: Request, role: str = Depends(verify_office_role)):
    """Serve the Canvassing Targets dashboard."""
    conn = get_connection()
    try:
        cursor = conn.execute('''
            SELECT 
                s.zipcode,
                COUNT(s.id) as event_count,
                MAX(s.event_date) as last_event_date,
                MAX(s.hail_size_inches) as max_hail,
                COUNT(DISTINCT j.id) as total_leads,
                SUM(CASE WHEN j.status = 'LEAD_CAPTURED' THEN 1 ELSE 0 END) as active_leads
            FROM storm_events s
            LEFT JOIN jobs j ON j.postal_code = s.zipcode
            GROUP BY s.zipcode
            ORDER BY last_event_date DESC
            LIMIT 50
        ''')
        targets = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(request, "admin_canvassing.html", {
        "request": request,
        "role": role,
        "targets": targets,
        "auth_token": request.cookies.get("auth_token", "")
    })

@app.get("/office/completed-jobs", tags=["frontend"])
async def serve_completed_jobs(request: Request, role: str = Depends(verify_office_role)):
    """Serve the Completed Jobs Archive for core team members (Admin, Operations, Accounting)."""
    from app.core.database import get_completed_jobs
    completed = await asyncio.to_thread(get_completed_jobs)
    return templates.TemplateResponse(request, "completed_jobs.html", {
        "request": request,
        "jobs": completed,
        "role": role,
        "active_page": "admin",
        "auth_token": request.cookies.get("auth_token", ""),
    })


@app.get("/office/jobs/{job_id}", tags=["frontend"])
async def serve_job_detail(request: Request, job_id: str, role: str = Depends(verify_field)):
    """Serve the unified Job Overview dashboard (for Admin, Operations, Accounting)."""
    from fastapi import HTTPException
    job = await asyncio.to_thread(_fetch_job_sync, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
        
    from app.api.auth import decode_token, is_core_user
    token = request.cookies.get("auth_token", "")
    claims = {}
    if token:
        try:
            claims = decode_token(token)
        except Exception:
            pass
    is_core = is_core_user(claims)

    from app.core.database import standardize_existing_job_documents
    await asyncio.to_thread(standardize_existing_job_documents, job_id)

    documents = await asyncio.to_thread(get_job_documents, job_id)
    if not is_core:
        documents = [d for d in documents if d.get("visibility") == "field_safe"]
    job["documents"] = documents

    from app.core.database import get_financials
    financials = await asyncio.to_thread(get_financials, job_id)
    job["financials"] = financials

    if job.get("damage_signals"):
        import json
        try:
            job["damage_signals"] = json.loads(job["damage_signals"])
        except Exception:
            job["damage_signals"] = []
    else:
        job["damage_signals"] = []

    # Fetch supplement flags for Forensic Summary card
    def _fetch_supplement_flags(jid: str) -> list[dict]:
        LABEL_MAP = {
            "RFG START": ("Starter Strip Shingles", "Manufacturer High-Wind Installation Spec"),
            "DMO PU": ("Roof Tear-Off Debris Pickup & Haul-Off", "Debris Tonnage & Disposal Compliance"),
            "RFG DRIP": ("Drip Edge Metal Flashing", "IRC R905.2.8.5 Building Code"),
            "RFG IWS": ("Ice & Water Shield Membrane", "IRC R905.1.2 Climate Code"),
            "MATH": ("Carrier Line-Item Calculation Shortage", "Audit Discrepancy Verification"),
        }
        _conn = get_connection()
        try:
            _cur = _conn.execute(
                """SELECT r.required_child_code, r.citation_text, r.citation_type 
                   FROM supplement_flags f
                   JOIN supplement_rules r ON r.id = f.rule_id
                   WHERE f.job_id = ? AND f.triggered = 1""",
                (jid,)
            )
            items = []
            for row in _cur.fetchall():
                code = row["required_child_code"]
                citation = row["citation_text"] or ""
                citation_type = row["citation_type"] or ""
                title, default_citation = LABEL_MAP.get(code, (code, citation))
                items.append({
                    "code": code,
                    "title": title,
                    "citation": citation or default_citation,
                    "citation_type": citation_type,
                })
            return items
        except Exception:
            return []
        finally:
            _conn.close()
    job["supplement_flags"] = await asyncio.to_thread(_fetch_supplement_flags, job_id)

    # Fetch schedule (crew, install_date) for display
    from app.core.database import get_job_schedule
    schedule = await asyncio.to_thread(get_job_schedule, job_id)
    job["schedule"] = schedule

    # Fetch Suggested Dates of Loss
    storm_events = []
    if job.get("postal_code"):
        import json
        from pathlib import Path
        zip_path = Path(__file__).resolve().parent.parent / "data" / "zipcodes.json"
        zipcodes = {}
        if zip_path.exists():
            try:
                with open(zip_path, 'r', encoding='utf-8') as zf:
                    zipcodes = json.load(zf)
            except Exception:
                pass
        job_coords = zipcodes.get(job["postal_code"])

        conn = get_connection()
        try:
            cursor = conn.execute(
                "SELECT id, zipcode, event_type, event_date, MAX(hail_size_inches) as hail_size_inches, MAX(wind_speed_mph) as wind_speed_mph, source, latitude, longitude FROM storm_events WHERE zipcode = ? GROUP BY event_date, event_type ORDER BY event_date DESC LIMIT 5",
                (job["postal_code"],)
            )
            raw_events = [dict(r) for r in cursor.fetchall()]
            for e in raw_events:
                raw_type = str(e.get("event_type", "")).upper()
                hail = e.get("hail_size_inches") or 0.0
                wind = e.get("wind_speed_mph") or 0.0

                if "HAIL" in raw_type or hail > 0:
                    label = "Hail Event"
                    metric = f"{hail:.2f}\" Hail" if hail > 0 else "Hail Verified"
                    badge_class = "bg-amber-900/80 text-amber-300 border-amber-600"
                    category = "HAIL"
                elif "GST" in raw_type:
                    label = "Severe Wind Gust"
                    metric = f"{wind:.0f} mph Gust" if wind > 0 else "Severe Gust"
                    badge_class = "bg-blue-900/80 text-blue-300 border-blue-600"
                    category = "WIND_GUST"
                else:
                    label = "Thunderstorm Wind Damage"
                    metric = f"{wind:.0f} mph Wind" if wind > 0 else "Wind Damage"
                    badge_class = "bg-red-900/80 text-red-300 border-red-600"
                    category = "WIND_DAMAGE"

                # Calculate distance proximity
                proximity = "Within service area"
                if job_coords and e.get("latitude") is not None and e.get("longitude") is not None:
                    import math
                    try:
                        lat1, lon1 = float(job_coords["lat"]), float(job_coords["lon"])
                        lat2, lon2 = float(e["latitude"]), float(e["longitude"])
                        
                        dlat = math.radians(lat2 - lat1)
                        dlon = math.radians(lon2 - lon1)
                        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
                        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
                        dist = 3958.8 * c
                        proximity = f"{dist:.1f} miles away"
                    except Exception:
                        pass

                e["display_label"] = label
                e["display_metric"] = metric
                e["badge_class"] = badge_class
                e["category"] = category
                e["formatted_date"] = str(e.get("event_date", ""))[:10]
                e["proximity"] = proximity
                storm_events.append(e)
        finally:
            conn.close()

    return templates.TemplateResponse(request, "job_detail.html", {
        "request": request, 
        "job": job,
        "storm_events": storm_events,
        "role": role,
        "is_core": is_core,
        "auth_token": request.cookies.get("auth_token", ""),
        "office_token": request.cookies.get("auth_token", "")
    })
