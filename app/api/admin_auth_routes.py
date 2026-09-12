"""
Admin Auth & Session Revocation API.

Allows administrators to immediately revoke issued JWT sessions (e.g. for offboarding or a lost device).
Does NOT alter or touch rep PINs, PIN hashes, PIN storage, or PIN verification logic.
Operates exclusively on issued JWT metadata (jti claim).
"""

import jwt
import structlog
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.api.auth import verify_admin
from app.core.database import revoke_token

logger = structlog.get_logger("app.api.admin_auth")
router = APIRouter(
    prefix="/api/admin/auth",
    tags=["admin-auth"],
    dependencies=[Depends(verify_admin)],
)


@router.post("/revoke", response_class=JSONResponse)
def revoke_session(
    payload: dict = Body(...),
    _=Depends(verify_admin),
):
    """
    Revoke an active JWT token/session immediately.
    Body: {"jti": "<uuid>"} or {"token": "<jwt_string>"}
    """
    jti = payload.get("jti")
    token = payload.get("token")
    expires_at = payload.get("expires_at")

    if not jti and not token:
        raise HTTPException(
            status_code=400,
            detail="Either 'jti' or 'token' must be provided to revoke a session."
        )

    if not jti and token:
        try:
            decoded = jwt.decode(token, options={"verify_signature": False})
            jti = decoded.get("jti")
            if not jti:
                raise HTTPException(
                    status_code=400,
                    detail="Provided token does not contain a 'jti' claim."
                )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid token format: {exc}")

    revoke_token(jti, expires_at=expires_at)
    logger.info("session_revoked_by_admin", jti=jti)
    return {"status": "success", "revoked_jti": jti}
