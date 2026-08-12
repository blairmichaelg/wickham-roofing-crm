"""
Frontend Routes: Serves all HTML templates and dashboards.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.auth import (
    decode_token,
    get_current_role,
    is_core_user,
    verify_field,
    verify_office_role,
)
from app.core.database import (
    _fetch_job_sync,
    get_completed_jobs,
    get_connection,
    get_field_rep_by_pin,
    get_financials,
    get_job_documents,
    get_job_schedule,
    list_field_reps,
    standardize_existing_job_documents,
)

router = APIRouter(tags=["frontend"])
logger = structlog.get_logger("app.api.frontend_routes")


@router.get("/login", tags=["frontend"])
async def serve_login(request: Request, redirect_url: str = "/"):
    """Serve the universal login page with optional post-auth redirect target."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "redirect_url": redirect_url},
    )


@router.post("/auth/login", tags=["frontend"])
async def process_login_form(
    request: Request,
    pin: str = Form(...),
    redirect_url: str = Form(default="/"),
):
    """Process login form (used by test suite via /auth/login)."""
    from app.api.auth import create_access_token
    from app.config import get_settings

    templates = request.app.state.templates
    settings = get_settings()
    role = None
    target_url = redirect_url or "/"
    rep_name: str | None = None
    rep_id: str | None = None

    if pin == settings.admin_pin:
        role = "admin"
        rep_name = "Michael"
        rep_id = "rep-michael"
        target_url = "/admin"
    elif pin == settings.accounting_pin:
        role = "accounting"
        rep_name = "Debi"
        rep_id = "rep-debi"
        target_url = "/accounting"
    elif pin == settings.operations_pin:
        role = "operations"
        rep_name = "Scott"
        rep_id = "rep-scott"
        target_url = "/api/operations/board"
    else:
        rep = get_field_rep_by_pin(pin)
        if rep:
            role = "field"
            rep_name = rep["name"]
            rep_id = rep["id"]
            target_url = "/field"

    if role:
        token = create_access_token(
            role,
            rep_name=rep_name,
            rep_id=rep_id,
        )
        response = RedirectResponse(url=target_url, status_code=303)
        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=True,
            secure=(settings.app_env == "production"),
            samesite="lax",
        )
        return response

    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "Invalid Access Code"}
    )


@router.post("/login", tags=["frontend"])
async def process_login(request: Request, access_code: str = Form(...)):
    """Process login and route to persona dashboard based on PIN."""
    from app.api.auth import create_access_token
    from app.config import get_settings

    templates = request.app.state.templates
    settings = get_settings()
    role = None
    redirect_url = "/"
    rep_name: str | None = None
    rep_id: str | None = None

    if access_code == settings.admin_pin:
        role = "admin"
        rep_name = "Michael"
        rep_id = "rep-michael"
        redirect_url = "/admin"
    elif access_code == settings.accounting_pin:
        role = "accounting"
        rep_name = "Debi"
        rep_id = "rep-debi"
        redirect_url = "/accounting"
    elif access_code == settings.operations_pin:
        role = "operations"
        rep_name = "Scott"
        rep_id = "rep-scott"
        redirect_url = "/api/operations/board"
    else:
        rep = get_field_rep_by_pin(access_code)
        if rep:
            role = "field"
            rep_name = rep["name"]
            rep_id = rep["id"]
            redirect_url = "/field"

    if role:
        token = create_access_token(
            role,
            rep_name=rep_name,
            rep_id=rep_id,
        )
        response = RedirectResponse(url=redirect_url, status_code=303)
        response.set_cookie(
            key="auth_token",
            value=token,
            httponly=True,
            secure=(settings.app_env == "production"),
            samesite="lax",
        )
        return response

    return templates.TemplateResponse(
        request, "login.html", {"request": request, "error": "Invalid Access Code"}
    )


@router.get("/office", tags=["frontend"])
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


@router.get("/help", tags=["frontend"])
async def help_page(request: Request, role: str = Depends(get_current_role)):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request, "help.html", {"request": request, "role": role}
    )


@router.get("/field", tags=["frontend"])
async def serve_field_app(request: Request, role: str = Depends(verify_field)):
    """Serve the Wickham Roofing Field App."""
    templates = request.app.state.templates
    reps = await asyncio.to_thread(list_field_reps, False)
    token = request.cookies.get("auth_token", "")
    current_rep_name = None
    claims: dict = {}
    if token:
        try:
            claims = decode_token(token)
            current_rep_name = claims.get("rep_name")
        except Exception:
            pass
    is_core = is_core_user(claims)

    return templates.TemplateResponse(
        request,
        "field_app.html",
        {
            "request": request,
            "reps": reps,
            "role": role,
            "is_core": is_core,
            "current_rep_name": current_rep_name,
            "active_page": "field",
            "field_token": token,
        },
    )


def _fetch_active_jobs_sync() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            SELECT id, invoice_id, homeowner_name, address_line1, city, state,
                   status, created_at, canvasser_name, supplement_sent_at, carrier_sla_days
            FROM jobs
            WHERE status != 'CLOSED'
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in cursor]
    finally:
        conn.close()


@router.get("/admin", tags=["frontend"])
async def serve_admin_dashboard(
    request: Request, role: str = Depends(verify_office_role)
):
    """Serve the Admin Kanban Board."""
    templates = request.app.state.templates
    jobs = await asyncio.to_thread(_fetch_active_jobs_sync)
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "request": request,
            "jobs": jobs,
            "active_page": "admin",
            "auth_token": request.cookies.get("auth_token", ""),
        },
    )


@router.get("/admin/reps", tags=["frontend"])
async def admin_reps_page(
    request: Request, role: str = Depends(verify_office_role)
):
    """Serve the Field Rep Management page."""
    templates = request.app.state.templates
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


@router.get("/accounting", tags=["frontend"])
async def serve_accounting_dashboard(
    request: Request, role: str = Depends(verify_office_role)
):
    """Serve the Accounting Ledger."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "accounting_dashboard.html",
        {
            "request": request,
            "active_page": "accounting",
            "auth_token": request.cookies.get("auth_token", ""),
        },
    )


@router.get("/office/canvassing", tags=["frontend"])
async def serve_canvassing(
    request: Request, role: str = Depends(verify_office_role)
):
    """Serve the Canvassing Targets dashboard."""
    templates = request.app.state.templates
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
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
            """
        )
        targets = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "admin_canvassing.html",
        {
            "request": request,
            "role": role,
            "targets": targets,
            "auth_token": request.cookies.get("auth_token", ""),
        },
    )


@router.get("/office/completed-jobs", tags=["frontend"])
async def serve_completed_jobs(
    request: Request, role: str = Depends(verify_office_role)
):
    """Serve the Completed Jobs Archive for core team members."""
    templates = request.app.state.templates
    completed = await asyncio.to_thread(get_completed_jobs)
    return templates.TemplateResponse(
        request,
        "completed_jobs.html",
        {
            "request": request,
            "jobs": completed,
            "role": role,
            "active_page": "admin",
            "auth_token": request.cookies.get("auth_token", ""),
        },
    )


@router.get("/office/jobs/{job_id}", tags=["frontend"])
async def serve_job_detail(
    request: Request, job_id: str, role: str = Depends(verify_field)
):
    """Serve the unified Job Overview dashboard."""
    templates = request.app.state.templates
    job = await asyncio.to_thread(_fetch_job_sync, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    token = request.cookies.get("auth_token", "")
    claims: dict = {}
    if token:
        try:
            claims = decode_token(token)
        except Exception:
            pass
    is_core = is_core_user(claims)

    await asyncio.to_thread(standardize_existing_job_documents, job_id)
    documents = await asyncio.to_thread(get_job_documents, job_id)
    if not is_core:
        documents = [d for d in documents if d.get("visibility") == "field_safe"]
    job["documents"] = documents

    financials = await asyncio.to_thread(get_financials, job_id)
    job["financials"] = financials

    if job.get("damage_signals"):
        try:
            job["damage_signals"] = json.loads(job["damage_signals"])
        except Exception:
            job["damage_signals"] = []
    else:
        job["damage_signals"] = []

    # Supplement flags for Forensic Summary card
    def _fetch_supplement_flags(jid: str) -> list[dict]:
        LABEL_MAP = {
            "RFG START": (
                "Starter Strip Shingles",
                "Manufacturer High-Wind Installation Spec",
            ),
            "DMO PU": (
                "Roof Tear-Off Debris Pickup & Haul-Off",
                "Debris Tonnage & Disposal Compliance",
            ),
            "RFG DRIP": (
                "Drip Edge Metal Flashing",
                "IRC R905.2.8.5 Building Code",
            ),
            "RFG IWS": (
                "Ice & Water Shield Membrane",
                "IRC R905.1.2 Climate Code",
            ),
            "MATH": (
                "Carrier Line-Item Calculation Shortage",
                "Audit Discrepancy Verification",
            ),
        }
        _conn = get_connection()
        try:
            _cur = _conn.execute(
                """SELECT r.required_child_code, r.citation_text, r.citation_type
                   FROM supplement_flags f
                   JOIN supplement_rules r ON r.id = f.rule_id
                   WHERE f.job_id = ? AND f.triggered = 1""",
                (jid,),
            )
            items = []
            for row in _cur.fetchall():
                code = row["required_child_code"]
                citation = row["citation_text"] or ""
                citation_type = row["citation_type"] or ""
                title, default_citation = LABEL_MAP.get(code, (code, citation))
                items.append(
                    {
                        "code": code,
                        "title": title,
                        "citation": citation or default_citation,
                        "citation_type": citation_type,
                    }
                )
            return items
        except Exception:
            return []
        finally:
            _conn.close()

    job["supplement_flags"] = await asyncio.to_thread(
        _fetch_supplement_flags, job_id
    )

    schedule = await asyncio.to_thread(get_job_schedule, job_id)
    job["schedule"] = schedule

    # Storm events for Suggested Dates of Loss
    storm_events: list[dict] = []
    if job.get("postal_code"):
        zip_path = (
            Path(__file__).resolve().parent.parent.parent / "data" / "zipcodes.json"
        )
        zipcodes: dict = {}
        if zip_path.exists():
            try:
                with open(zip_path, encoding="utf-8") as zf:
                    zipcodes = json.load(zf)
            except Exception:
                pass
        job_coords = zipcodes.get(job["postal_code"])

        conn = get_connection()
        try:
            cursor = conn.execute(
                """SELECT id, zipcode, event_type, event_date,
                          MAX(hail_size_inches) as hail_size_inches,
                          MAX(wind_speed_mph) as wind_speed_mph,
                          source, latitude, longitude
                   FROM storm_events WHERE zipcode = ?
                   GROUP BY event_date, event_type
                   ORDER BY event_date DESC LIMIT 5""",
                (job["postal_code"],),
            )
            raw_events = [dict(r) for r in cursor.fetchall()]
            for e in raw_events:
                raw_type = str(e.get("event_type", "")).upper()
                hail = e.get("hail_size_inches") or 0.0
                wind = e.get("wind_speed_mph") or 0.0

                if "HAIL" in raw_type or hail > 0:
                    label = "Hail Event"
                    metric = f'{hail:.2f}" Hail' if hail > 0 else "Hail Verified"
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

                proximity = "Within service area"
                if (
                    job_coords
                    and e.get("latitude") is not None
                    and e.get("longitude") is not None
                ):
                    try:
                        lat1, lon1 = float(job_coords["lat"]), float(job_coords["lon"])
                        lat2, lon2 = float(e["latitude"]), float(e["longitude"])
                        dlat = math.radians(lat2 - lat1)
                        dlon = math.radians(lon2 - lon1)
                        a = (
                            math.sin(dlat / 2) ** 2
                            + math.cos(math.radians(lat1))
                            * math.cos(math.radians(lat2))
                            * math.sin(dlon / 2) ** 2
                        )
                        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
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

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "request": request,
            "job": job,
            "storm_events": storm_events,
            "role": role,
            "is_core": is_core,
            "auth_token": request.cookies.get("auth_token", ""),
            "office_token": request.cookies.get("auth_token", ""),
        },
    )
