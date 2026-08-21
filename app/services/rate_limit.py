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

async def check_rate_limit(request: Request) -> str:
    """
    Dependency that enforces a sliding window rate limit per IP.
    Returns the IP address if successful, raises 429 if limit exceeded.
    """
    client_ip = request.client.host if request.client else "unknown"
    # Fallback to x-forwarded-for if behind proxy (e.g. cloudflare)
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

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

def reset_rate_limits() -> None:
    """For testing purposes."""
    global _request_history
    _request_history.clear()
