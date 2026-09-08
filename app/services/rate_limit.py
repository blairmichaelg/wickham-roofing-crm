import asyncio
import time
from collections import defaultdict

import structlog
from fastapi import HTTPException, Request

logger = structlog.get_logger("app.services.rate_limit")

# Store timestamp of requests for each IP
# Format: { "ip_address": [timestamp1, timestamp2, ...] }
_request_history: defaultdict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()

RATE_LIMIT_REQUESTS = 3
RATE_LIMIT_WINDOW_SECONDS = 10

LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_LOCKOUT_WINDOW_SECONDS = 60

# Store timestamps of failed login attempts per IP
_failed_login_attempts: defaultdict[str, list[float]] = defaultdict(list)


def _get_client_ip(request: Request) -> str:
    """Extracts client IP with proxy-aware x-forwarded-for header support."""
    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    return client_ip


async def check_rate_limit(request: Request) -> str:
    """
    Dependency that enforces a sliding window rate limit per IP.
    Returns the IP address if successful, raises 429 if limit exceeded.
    """
    client_ip = _get_client_ip(request)
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    async with _lock:
        # Filter out old requests
        history = _request_history[client_ip]
        valid_requests = [ts for ts in history if ts > cutoff]
        
        if len(valid_requests) >= RATE_LIMIT_REQUESTS:
            logger.warning("rate_limit_exceeded", ip=client_ip, requests=len(valid_requests))
            raise HTTPException(status_code=429, detail="Too Many Requests")
            
        valid_requests.append(now)
        _request_history[client_ip] = valid_requests
        
    return client_ip


async def check_login_rate_limit(request: Request) -> None:
    """
    Enforces brute-force lockout on authentication endpoints.
    Raises HTTPException 429 if client IP has exceeded max failed attempts.
    """
    client_ip = _get_client_ip(request)
    now = time.time()
    cutoff = now - LOGIN_LOCKOUT_WINDOW_SECONDS

    async with _lock:
        attempts = [ts for ts in _failed_login_attempts[client_ip] if ts > cutoff]
        _failed_login_attempts[client_ip] = attempts

        if len(attempts) >= LOGIN_MAX_FAILED_ATTEMPTS:
            logger.warning("login_lockout_active", ip=client_ip, failed_attempts=len(attempts))
            raise HTTPException(
                status_code=429,
                detail=f"Too many failed login attempts. Please wait {LOGIN_LOCKOUT_WINDOW_SECONDS} seconds before trying again."
            )


async def record_login_failure(request: Request) -> None:
    """Records a failed authentication attempt timestamp for the client IP."""
    client_ip = _get_client_ip(request)
    now = time.time()
    cutoff = now - LOGIN_LOCKOUT_WINDOW_SECONDS

    async with _lock:
        attempts = [ts for ts in _failed_login_attempts[client_ip] if ts > cutoff]
        attempts.append(now)
        _failed_login_attempts[client_ip] = attempts
        logger.warning("login_failure_recorded", ip=client_ip, total_recent_failures=len(attempts))


async def record_login_success(request: Request) -> None:
    """Clears failed login attempt history for the client IP upon successful authentication."""
    client_ip = _get_client_ip(request)
    async with _lock:
        _failed_login_attempts.pop(client_ip, None)


def reset_rate_limits() -> None:
    """For testing purposes: resets general API rate limits."""
    global _request_history
    _request_history.clear()


def reset_login_rate_limits() -> None:
    """For testing purposes: resets auth lockout and failed login tracking."""
    global _failed_login_attempts
    _failed_login_attempts.clear()

