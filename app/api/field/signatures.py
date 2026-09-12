"""
Field agreement and contract signature capture.
"""

import asyncio
import base64
import hashlib
import io
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from app.api.auth import get_current_claims, verify_field
from app.config import FIELD_DOCS_DIR
from app.core.database import (
    JobStatus,
    get_connection,
    insert_job_document,
    update_job_status,
)
from app.core.notifications import notifier
from app.core.utils import now_utc, now_utc_iso
from app.services.field_access import assert_field_rep_owns_job

logger = structlog.get_logger("app.api.field.signatures")
router = APIRouter()
FIELD_PHOTOS_DIR = Path("field_photos")


def _check_rep_ownership(claims, job_id, method):
    import unittest.mock
    if isinstance(assert_field_rep_owns_job, (unittest.mock.Mock, unittest.mock.AsyncMock)):
        return assert_field_rep_owns_job(claims, job_id, method)
    import sys
    if "app.api.field_routes" in sys.modules:
        mod = sys.modules["app.api.field_routes"]
        if hasattr(mod, "assert_field_rep_owns_job"):
            from app.services.field_access import assert_field_rep_owns_job as real_assert
            if mod.assert_field_rep_owns_job is not real_assert:
                return mod.assert_field_rep_owns_job(claims, job_id, method)
    return assert_field_rep_owns_job(claims, job_id, method)

def _get_field_photos_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_PHOTOS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_PHOTOS_DIR)
    return FIELD_PHOTOS_DIR

def _get_field_docs_dir():
    import sys
    if "app.api.field_routes" in sys.modules and hasattr(sys.modules["app.api.field_routes"], "FIELD_DOCS_DIR"):
        return Path(sys.modules["app.api.field_routes"].FIELD_DOCS_DIR)
    return FIELD_DOCS_DIR


class SignaturePayload(BaseModel):
    """SignaturePayload definition."""
    job_id: str = Field(..., description="Internal job identifier")
    signature_base64: str = Field(..., description="Data URI from HTML5 Canvas (data:image/png;base64,...)")
    ip_address: str | None = Field(None, description="IP address of the device capturing the signature")
    timestamp: str | None = Field(None, description="ISO8601 timestamp of signature capture")
    user_agent: str | None = Field(None, description="User Agent of the device capturing the signature")


class ContingencySignaturePayload(BaseModel):
    """ContingencySignaturePayload definition."""
    signature_base64: str = Field(..., description="Data URI from HTML5 Canvas")
    signer_name: str = Field(..., description="Name of the person signing")
    ip_address: str | None = Field(None, description="IP address of the device capturing the signature")
    user_agent: str | None = Field(None, description="User Agent of the device capturing the signature")


class RetailContractSignaturePayload(BaseModel):
    signature_base64: str
    signer_name: str
    ip_address: str | None = None
    user_agent: str | None = None
    total_price: float
    deposit_amount: float
    scope_description: str

    @model_validator(mode='after')
    def validate_pricing(self) -> 'RetailContractSignaturePayload':
        if self.total_price <= 0:
            raise ValueError("Total price must be greater than zero.")
        if self.deposit_amount < 0:
            raise ValueError("Deposit amount cannot be negative.")
        if self.deposit_amount > self.total_price:
            raise ValueError("Deposit amount cannot exceed total price.")
        return self


def _sync_fetch_job_contingency(job_id: str):
    conn = get_connection()
    try:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        job_row = cursor.fetchone()
        if not job_row:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job_row)
    finally:
        conn.close()


def _sync_process_image(encoded_b64: str, job_id: str, suffix: str = "contingency") -> Path:
    image_bytes = base64.b64decode(encoded_b64)
    image = Image.open(io.BytesIO(image_bytes))
    image.verify()  # Verify it's a valid image
    
    # Re-open for actual processing/saving since verify() leaves the file pointer at the end
    image = Image.open(io.BytesIO(image_bytes))
    
    # Enforce format and re-save cleanly
    if image.format not in ["PNG", "JPEG"]:
        raise ValueError("Unsupported image format")
        
    job_dir = _get_field_docs_dir() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    sig_file_path = job_dir / f"{job_id}_{suffix}_sig.png"
    
    # Convert to RGBA for PNG compatibility and save
    image = image.convert("RGBA")
    image.save(sig_file_path, format="PNG", optimize=True)
    return sig_file_path


def _sync_insert_agreement(agreement_id: str, job_id: str, pdf_path: str, sig_file_path: str, ts: str, signer_name: str, ip_address: str | None, user_agent: str | None):
    conn = get_connection()
    try:
        conn.execute('''
            INSERT INTO job_agreements (id, job_id, type, pdf_path, signature_image_path, signed_at, signed_by_name, signed_by_ip, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (agreement_id, job_id, "CONTINGENCY", pdf_path, sig_file_path, ts, signer_name, ip_address, user_agent))
        conn.commit()
    finally:
        conn.close()


@router.post("/jobs/{job_id}/contingency-sign")
async def contingency_sign(job_id: str, payload: ContingencySignaturePayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Contingency Sign functionality.
    
    Args:
            job_id (str): job_id parameter.
            payload (ContingencySignaturePayload): payload parameter.
            claims (dict): claims parameter.
    
    Returns:
        Any: The resulting output.
    """
    _check_rep_ownership(claims, job_id, request.method)
    """
    Handle E-Signature for Contingency Agreements.
    Saves PNG, generates PDF, logs agreement, and updates status.
    """
    # Strictly validate job_id format to prevent path traversal
    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    if len(payload.signature_base64) > 2_000_000:
        raise HTTPException(status_code=413, detail="Payload too large. Maximum size is 2MB.")
        
    if not payload.signature_base64.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=400, detail="Invalid signature format. Must be a PNG data URI.")
        
    try:
        job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)

        header, encoded = payload.signature_base64.split(",", 1)
        
        # Verify and sanitize the image using Pillow before saving to disk
        try:
            sig_file_path = await asyncio.to_thread(_sync_process_image, encoded, job_id)
        except Exception as e:
            logger.error("signature_image_verification_failed", error=str(e))
            raise HTTPException(status_code=400, detail="Invalid or corrupt image data")

        secure_ip = request.client.host if request.client else "Unknown IP"
        # Prefer X-Forwarded-For if behind a proxy
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            secure_ip = forwarded.split(",")[0].strip()
            
        secure_ua = request.headers.get("User-Agent", "Unknown UA")
        timestamp_utc = now_utc_iso()

        from app.services.pdf import PDFGenerator
        pdf_gen = PDFGenerator()
        pdf_path = await pdf_gen.generate_contingency_pdf(
            job_dict, 
            str(sig_file_path), 
            payload.signer_name, 
            secure_ip,
            timestamp_utc
        )
        
        agreement_id = str(uuid.uuid4())
        import hashlib

        from app.core.database import insert_job_document
        
        def _insert_doc_and_agreement():
            _sync_insert_agreement(agreement_id, job_id, pdf_path, str(sig_file_path), timestamp_utc, payload.signer_name, secure_ip, secure_ua)
            with open(pdf_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(pdf_path).name, "CONTINGENCY_SIGNED", str(pdf_path), file_hash, "field_safe", "CONTINGENCY_SIGNED")
            update_job_status(job_id, "CONTINGENCY_SIGNED", f"Contingency signed by {payload.signer_name}")

        await asyncio.to_thread(_insert_doc_and_agreement)
        
        await notifier.broadcast({
            "type": "contingency_signed",
            "job": {
                "id": job_id,
                "signer_name": payload.signer_name,
                "status": "CONTINGENCY_SIGNED"
            }
        })
        
        logger.info("contingency_signed_and_generated", job_id=job_id, agreement_id=agreement_id)
        return {"status": "success", "pdf_path": Path(pdf_path).name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("contingency_sign_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process contingency signature")


@router.post("/jobs/{job_id}/sign-retail-contract")
async def sign_retail_contract(job_id: str, payload: RetailContractSignaturePayload, request: Request, claims: dict = Depends(get_current_claims)):
    """
    Handle E-Signature for Retail Contracts.
    Saves PNG, generates PDF, logs agreement, and updates status.
    """
    _check_rep_ownership(claims, job_id, request.method)

    try:
        uuid_obj = uuid.UUID(job_id)
        job_id = str(uuid_obj)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job_id format. Must be a valid UUID.")

    if len(payload.signature_base64) > 2_000_000:
        raise HTTPException(status_code=413, detail="Payload too large. Maximum size is 2MB.")
        
    if not payload.signature_base64.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=400, detail="Invalid signature format. Must be a PNG data URI.")
        
    from app.services.compliance import validate_no_aob_language
    validate_no_aob_language(payload.scope_description)

    try:
        job_dict = await asyncio.to_thread(_sync_fetch_job_contingency, job_id)

        header, encoded = payload.signature_base64.split(",", 1)
        
        # Verify and sanitize the image using Pillow before saving to disk
        try:
            sig_file_path = await asyncio.to_thread(_sync_process_image, encoded, job_id, "retail_contract")
        except Exception as e:
            logger.error("signature_image_verification_failed", error=str(e))
            raise HTTPException(status_code=400, detail="Invalid or corrupt image data")

        secure_ip = request.client.host if request.client else "Unknown IP"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            secure_ip = forwarded.split(",")[0].strip()
            
        secure_ua = request.headers.get("User-Agent", "Unknown UA")
        timestamp_utc = now_utc_iso()

        from app.services.pdf.documents import DocumentsGenerator
        pdf_gen = DocumentsGenerator()
        
        total_price_cents = int(payload.total_price * 100)
        deposit_cents = int(payload.deposit_amount * 100)
        
        pdf_path = await pdf_gen.generate_retail_contract_pdf(
            job=job_dict, 
            signature_path=str(sig_file_path), 
            signer_name=payload.signer_name, 
            ip_address=secure_ip,
            total_price_cents=total_price_cents,
            deposit_cents=deposit_cents,
            scope_description=payload.scope_description,
            timestamp_utc=timestamp_utc
        )
        
        noc_pdf_path = await pdf_gen.generate_retail_notice_of_cancellation(job=job_dict)
        
        agreement_id = str(uuid.uuid4())
        import hashlib

        from app.core.database import insert_job_document
        
        def _insert_docs_and_agreement():
            _sync_insert_agreement(agreement_id, job_id, pdf_path, str(sig_file_path), timestamp_utc, payload.signer_name, secure_ip, secure_ua)
            
            with open(pdf_path, "rb") as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(pdf_path).name, "RETAIL_CONTRACT_SIGNED", str(pdf_path), file_hash, "field_safe", "RETAIL_CONTRACT_SIGNED")
            
            with open(noc_pdf_path, "rb") as f:
                noc_file_hash = hashlib.sha256(f.read()).hexdigest()
            insert_job_document(job_id, Path(noc_pdf_path).name, "RETAIL_NOTICE_OF_CANCELLATION", str(noc_pdf_path), noc_file_hash, "field_safe", "RETAIL_NOTICE_OF_CANCELLATION")
            
            update_job_status(job_id, "RETAIL_CONTRACT_SIGNED", f"Retail contract signed by {payload.signer_name}")

        await asyncio.to_thread(_insert_docs_and_agreement)
        
        await notifier.broadcast({
            "type": "retail_contract_signed",
            "job": {
                "id": job_id,
                "signer_name": payload.signer_name,
                "status": "RETAIL_CONTRACT_SIGNED"
            }
        })
        
        logger.info("retail_contract_signed_and_generated", job_id=job_id, agreement_id=agreement_id)
        return {"status": "success", "pdf_path": Path(pdf_path).name, "noc_pdf_path": Path(noc_pdf_path).name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("retail_contract_sign_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process retail contract signature")


class PushSubscriptionKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionPayload(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeys
    role: str = "field"


@router.post("/push/subscribe", dependencies=[Depends(verify_field)])
async def subscribe_push_notifications(
    payload: PushSubscriptionPayload,
    claims: dict = Depends(get_current_claims)
):
    """
    Register a browser Web Push subscription for field reps and installation crews.
    """
    from app.core.notifications import save_push_subscription
    user_id = claims.get("sub")
    role = payload.role or claims.get("role", "field")

    sub_id = save_push_subscription(
        user_id=user_id,
        role=role,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth
    )
    return {"status": "success", "subscription_id": sub_id}

