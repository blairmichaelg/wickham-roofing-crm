import datetime

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request

from app.config import get_settings

ALGORITHM = "HS256"

def create_access_token(
    role: str,
    rep_name: str | None = None,
    rep_id: str | None = None,
) -> str:
    """
    Create Access Token functionality.
    
    Args:
            role (str): role parameter.
            rep_name (str | None): rep_name parameter.
            rep_id (str | None): rep_id parameter.
    
    Returns:
        str: The resulting output.
    """
    settings = get_settings()
    expire = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=12)
    to_encode: dict = {"sub": role, "role": role, "exp": expire}
    if rep_name:
        to_encode["rep_name"] = rep_name
    if rep_id:
        to_encode["rep_id"] = rep_id
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> dict:
    """
    Decode a JWT and return the full payload dict.
    Raises HTTPException 401 on invalid/expired token.
    Returns at minimum: {"role": str}
    May also contain: {"rep_name": str, "rep_id": str}
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        # Support both legacy "sub" claim and new "role" claim
        role = payload.get("role") or payload.get("sub")
        if role is None:
            raise HTTPException(status_code=401, detail="Invalid authentication credentials")
        payload["role"] = role
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

FULL_ACCESS_CORE_NAMES = {"michael", "scott", "debi"}
READ_ONLY_CORE_NAMES = {"alex wickham"}

def is_core_user(claims: dict | None, method: str = "GET") -> bool:
    """
    Returns True if user is Admin/Accounting/Operations OR one of the core team members (Michael, Scott, Debi).
    Core team members always have full permission/view/access to all boards and functions.
    """
    if not claims:
        return False
    role = claims.get("role")
    if role in ["admin", "accounting", "operations"]:
        return True
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name in FULL_ACCESS_CORE_NAMES:
        return True
    if rep_name in READ_ONLY_CORE_NAMES:
        return method in ("GET", "HEAD", "OPTIONS")
    return False

def is_admin_or_core(claims: dict | None, method: str = "GET") -> bool:
    if not claims:
        return False
    if claims.get("role") == "admin":
        return True
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name in FULL_ACCESS_CORE_NAMES:
        return True
    if rep_name in READ_ONLY_CORE_NAMES:
        return method in ("GET", "HEAD", "OPTIONS")
    return False

def is_accounting_or_core(claims: dict | None, method: str = "GET") -> bool:
    if not claims:
        return False
    if claims.get("role") in ["admin", "accounting"]:
        return True
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name in FULL_ACCESS_CORE_NAMES:
        return True
    if rep_name in READ_ONLY_CORE_NAMES:
        return method in ("GET", "HEAD", "OPTIONS")
    return False

def is_operations_or_core(claims: dict | None, method: str = "GET") -> bool:
    if not claims:
        return False
    if claims.get("role") in ["admin", "operations"]:
        return True
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name in FULL_ACCESS_CORE_NAMES:
        return True
    if rep_name in READ_ONLY_CORE_NAMES:
        return method in ("GET", "HEAD", "OPTIONS")
    return False

def is_office_or_core(claims: dict | None, method: str = "GET") -> bool:
    if not claims:
        return False
    if claims.get("role") in ["admin", "operations", "accounting"]:
        return True
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name in FULL_ACCESS_CORE_NAMES:
        return True
    if rep_name in READ_ONLY_CORE_NAMES:
        return method in ("GET", "HEAD", "OPTIONS")
    return False

async def get_current_role(
    auth_token: str | None = Cookie(None),
    x_internal_token: str | None = Header(None, alias="x-internal-token")
) -> str:
    """Returns only the role string from the JWT. Used by all role-check dependencies."""
    # Support both cookie and header for API access
    token = x_internal_token or auth_token
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(token)
    return payload["role"]

async def get_current_claims(
    request: Request,
    auth_token: str | None = Cookie(None),
    x_internal_token: str | None = Header(None, alias="x-internal-token")
) -> dict:
    """
    Returns the full decoded JWT payload dict.
    Used by routes that need rep_name or rep_id in addition to the role.
    """
    token = x_internal_token or auth_token
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    claims = decode_token(token)
    
    rep_name = (claims.get("rep_name") or "").strip().lower()
    if rep_name == "alex wickham":
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            raise HTTPException(
                status_code=403,
                detail="Alex Wickham has read-only privileges. Read-only access: contact an admin to make this change."
            )
            
    return claims

async def verify_admin(request: Request, claims: dict = Depends(get_current_claims)):
    if not is_admin_or_core(claims, request.method):
        raise HTTPException(status_code=403, detail="Not authorized for admin access")
    return claims["role"]

async def verify_accounting(request: Request, claims: dict = Depends(get_current_claims)):
    if not is_accounting_or_core(claims, request.method):
        raise HTTPException(status_code=403, detail="Not authorized for accounting access")
    return claims["role"]

async def verify_operations(request: Request, claims: dict = Depends(get_current_claims)):
    if not is_operations_or_core(claims, request.method):
        raise HTTPException(status_code=403, detail="Not authorized for operations access")
    return claims["role"]

async def verify_field(claims: dict = Depends(get_current_claims)):
    return claims["role"]

async def verify_office_role(request: Request, claims: dict = Depends(get_current_claims)):
    if not is_office_or_core(claims, request.method):
        raise HTTPException(status_code=403, detail="Not authorized for office access")
    return claims["role"]
