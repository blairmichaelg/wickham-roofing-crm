"""
Shared domain and infrastructure constants.
"""

from enum import StrEnum


class StormEventType(StrEnum):
    """Canonical storm event types recognized by NOAA storm feeds and canvassing."""
    HAIL = "HAIL"
    WIND = "WIND"
    TORNADO = "TORNADO"


class ErrorCode(StrEnum):
    """Machine-readable error codes for API error responses."""
    DB_ERROR = "DB_ERROR"
    REDIS_UNAVAILABLE = "REDIS_UNAVAILABLE"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"
    OWNERSHIP_VIOLATION = "OWNERSHIP_VIOLATION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
