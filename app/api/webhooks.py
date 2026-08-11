"""
Generic Event Trigger Endpoint.

Receives POST requests, validates the shared API key via constant-time comparison,
and enqueues valid events into the ARQ Redis queue for async processing.
"""

import secrets

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import Settings, get_settings

logger = structlog.get_logger("app.api.webhooks")

router = APIRouter(prefix="/events", tags=["events"])


class EventPayload(BaseModel):
    """
    Generic event trigger payload.
    """
    job_id: str = Field(..., description="Target Job ID")
    event_type: str = Field(..., description="Event type: 'supplement' or 'inspection'")
    ev_pdf_path: str | None = Field(default=None, description="Path to EV PDF for supplements")
    sol_pdf_path: str | None = Field(default=None, description="Path to SoL PDF for supplements")


async def verify_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Validate the x-api-key header against the configured webhook secret.
    """
    if x_api_key is None:
        logger.warning("webhook_auth_missing_header")
        raise HTTPException(status_code=401, detail="Missing x-api-key header")

    if not secrets.compare_digest(x_api_key, settings.webhook_secret):
        logger.warning("webhook_auth_invalid_key")
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.post("/trigger", dependencies=[Depends(verify_api_key)])
async def receive_event_trigger(
    payload: EventPayload,
    request: Request
) -> dict:
    """
    Ingest a generic event and trigger ARQ background workers.
    """
    logger.info(
        "event_trigger_received",
        job_id=payload.job_id,
        event_type=payload.event_type,
    )

    redis_pool = request.app.state.redis_pool

    try:
        if payload.event_type == "supplement":
            if not payload.ev_pdf_path or not payload.sol_pdf_path:
                return {"status": "ignored", "reason": "missing_pdf_paths"}
            
            await redis_pool.enqueue_job(
                "process_supplement_event",
                job_id=payload.job_id,
                ev_pdf_path=payload.ev_pdf_path,
                sol_pdf_path=payload.sol_pdf_path,
                role="admin"
            )
        elif payload.event_type == "inspection":
            # Inspection payloads need to be hydrated into an InspectionJob first.
            # Currently unhandled directly from flat webhooks.
            logger.warning("inspection_event_not_fully_implemented_from_webhook")
            return {"status": "ignored", "reason": "inspection_event_not_implemented"}
        else:
            return {"status": "ignored", "reason": "unknown_event_type"}

    except Exception as e:
        logger.error(
            "event_enqueue_failed",
            job_id=payload.job_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=503, detail="Service Unavailable - Queue full or offline"
        )

    logger.info(
        "event_enqueued",
        job_id=payload.job_id,
        event_type=payload.event_type,
    )

    return {"status": "queued", "job_id": payload.job_id}
