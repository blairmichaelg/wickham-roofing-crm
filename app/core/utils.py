"""
app/core/utils.py — Pure utility functions with no I/O or framework deps.

These are thin helpers consumed across the application layer.
"""

from __future__ import annotations

from datetime import UTC, datetime


def days_since(date_str: str) -> int:
    """
    Return the number of calendar days since a date string.

    Accepts ISO-8601 strings produced by SQLite (both ``YYYY-MM-DD HH:MM:SS``
    and the extended ``YYYY-MM-DDTHH:MM:SSZ`` / ``+HH:MM`` variants).

    Returns 0 on any parse failure so callers never crash on bad data.
    """
    if not date_str:
        return 0
    try:
        date_str = date_str.removesuffix("Z")
        if "+" in date_str:
            date_str = date_str.split("+")[0]

        try:
            dt = datetime.fromisoformat(date_str)
        except ValueError:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

        return (datetime.now(UTC).replace(tzinfo=None) - dt.replace(tzinfo=None)).days
    except Exception:
        return 0
