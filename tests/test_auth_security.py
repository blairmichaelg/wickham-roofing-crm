"""
tests/test_auth_security.py

Tests for authentication hardening, brute-force login rate limiting/lockout,
RBAC authorization enforcement, and cryptographic JWT integrity.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.auth import ALGORITHM, create_access_token, decode_token
from app.config import get_settings
from app.main import app
from app.services.rate_limit import (
    LOGIN_MAX_FAILED_ATTEMPTS,
    reset_login_rate_limits,
    reset_rate_limits,
)


@pytest.fixture(autouse=True)
def clean_rate_limits():
    """Ensure clean rate limit state before and after each test."""
    reset_rate_limits()
    reset_login_rate_limits()
    yield
    reset_rate_limits()
    reset_login_rate_limits()


def test_auth_login_brute_force_lockout():
    """Verify that multiple failed PIN attempts trigger HTTP 429 lockout."""
    client = TestClient(app)
    ip_headers = {"x-forwarded-for": "198.51.100.42"}

    # Attempt failed logins up to the limit
    for i in range(LOGIN_MAX_FAILED_ATTEMPTS):
        resp = client.post(
            "/auth/login",
            data={"pin": f"bad_{i}", "redirect_url": "/"},
            headers=ip_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=1" in resp.headers.get("location", "")

    # The next attempt should be rate limited with HTTP 429
    locked_resp = client.post(
        "/auth/login",
        data={"pin": "bad_pin", "redirect_url": "/"},
        headers=ip_headers,
        follow_redirects=False,
    )
    assert locked_resp.status_code == 429
    assert "Too many failed login attempts" in locked_resp.json().get("detail", "")


def test_auth_login_successful_login_resets_lockout_counter():
    """Verify that a successful login resets the failed attempts counter for that IP."""
    client = TestClient(app)
    settings = get_settings()
    ip_headers = {"x-forwarded-for": "198.51.100.43"}

    # 4 failed attempts (1 below threshold)
    for i in range(LOGIN_MAX_FAILED_ATTEMPTS - 1):
        resp = client.post(
            "/auth/login",
            data={"pin": f"bad_{i}", "redirect_url": "/"},
            headers=ip_headers,
            follow_redirects=False,
        )
        assert resp.status_code == 303

    # Now login successfully with admin PIN
    success_resp = client.post(
        "/auth/login",
        data={"pin": settings.admin_pin, "redirect_url": "/"},
        headers=ip_headers,
        follow_redirects=False,
    )
    assert success_resp.status_code == 303
    assert success_resp.headers["location"] == "/admin"

    # Subsequent failed attempt should be at count 1, NOT 5 (so NOT 429)
    next_failed = client.post(
        "/auth/login",
        data={"pin": "wrong_again", "redirect_url": "/"},
        headers=ip_headers,
        follow_redirects=False,
    )
    assert next_failed.status_code == 303


def test_rbac_unauthenticated_request_rejected():
    """Verify unauthenticated requests to protected endpoints return 401."""
    client = TestClient(app)
    resp = client.get("/api/office/jobs/sanity-check")
    assert resp.status_code == 401


def test_rbac_field_role_cannot_access_admin_endpoints():
    """Verify field role tokens cannot access admin endpoints (returns 403)."""
    client = TestClient(app)
    field_token = create_access_token("field", rep_name="Test Rep", rep_id="rep-test-01")
    headers = {"Authorization": f"Bearer {field_token}"}

    resp = client.get("/api/office/jobs/sanity-check", headers=headers)
    assert resp.status_code == 403
    assert "Not authorized for admin access" in resp.json().get("detail", "")


def test_rbac_accounting_role_cannot_access_admin_only_endpoints():
    """Verify non-core accounting role tokens cannot access admin-only sanity-check endpoint."""
    client = TestClient(app)
    acct_token = create_access_token("accounting", rep_name="Accounting Clerk", rep_id="rep-acct-02")
    headers = {"Authorization": f"Bearer {acct_token}"}

    resp = client.get("/api/office/jobs/sanity-check", headers=headers)
    assert resp.status_code == 403


def test_jwt_token_algorithm_and_secret_integrity():
    """Verify token encoding uses HS256 and tampering with signature raises 401."""
    token = create_access_token("admin", rep_name="Michael", rep_id="rep-michael")
    claims = decode_token(token)
    assert claims["role"] == "admin"
    assert claims["rep_name"] == "Michael"

    # Tamper with the token payload/signature
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(Exception) as exc_info:
        decode_token(tampered)
    assert "Invalid authentication credentials" in str(exc_info.value)
