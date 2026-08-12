"""
app/main.py — Application entry point shim for backwards compatibility.
"""

from __future__ import annotations

from app.core.status_labels import STATUS_LABELS  # noqa: F401
from app.core.utils import days_since  # noqa: F401

# Re-export app + templates from server — `uvicorn app.main:app` works unchanged.
from app.server import app, templates  # noqa: F401

__all__ = ["app", "templates", "STATUS_LABELS", "days_since"]
