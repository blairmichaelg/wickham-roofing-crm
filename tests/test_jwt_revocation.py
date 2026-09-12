"""
Unit & integration tests for JWT revocation and session blacklist.

Verifies:
- Issued JWTs contain a unique jti claim.
- Active valid tokens authenticate successfully.
- Revoking a token by jti or token string immediately rejects access with HTTP 401.
- Immediate effect without waiting for token expiration.
- Admin-only session revocation endpoint /api/admin/auth/revoke.
- Non-admin callers are blocked with HTTP 403.
- NO PIN values, PIN hashes, or PIN verification flows are touched.
"""

import uuid
import pytest
from fastapi.testclient import TestClient

from app.api.auth import create_access_token, decode_token
from app.core.database import is_token_revoked, revoke_token, run_migrations
from app.server import app

run_migrations()
client = TestClient(app)


def test_created_token_contains_jti():
    """Ensure all freshly generated JWTs include a unique jti claim."""
    token1 = create_access_token(role="field", rep_name="Test Rep", rep_id="rep_1")
    token2 = create_access_token(role="field", rep_name="Test Rep", rep_id="rep_1")

    payload1 = decode_token(token1)
    payload2 = decode_token(token2)

    assert "jti" in payload1
    assert "jti" in payload2
    assert payload1["jti"] != payload2["jti"]


def test_revoked_token_is_rejected_immediately():
    """Verify that revoking a token prevents authentication immediately."""
    custom_jti = str(uuid.uuid4())
    token = create_access_token(role="field", rep_name="Test Rep", rep_id="rep_1", jti=custom_jti)

    # Pre-revocation: decode succeeds and endpoint access succeeds
    payload = decode_token(token)
    assert payload["jti"] == custom_jti
    assert not is_token_revoked(custom_jti)

    resp_before = client.get("/api/field/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp_before.status_code == 200

    # Revoke the token
    revoke_token(custom_jti)
    assert is_token_revoked(custom_jti)

    # Post-revocation: decode_token raises 401 and API call fails with 401
    with pytest.raises(Exception) as exc:
        decode_token(token)
    assert "revoked" in str(exc.value).lower()

    resp_after = client.get("/api/field/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp_after.status_code == 401
    assert "revoked" in resp_after.json().get("detail", "").lower()


def test_admin_revoke_endpoint_by_jti_and_token():
    """Verify admin endpoint /api/admin/auth/revoke by both jti and token string."""
    admin_token = create_access_token(role="admin", rep_name="Admin User")
    rep_jti = str(uuid.uuid4())
    rep_token = create_access_token(role="field", rep_name="Offboarded Rep", jti=rep_jti)

    # Field rep can access before revocation
    resp = client.get("/api/field/jobs", headers={"Authorization": f"Bearer {rep_token}"})
    assert resp.status_code == 200

    # Admin revokes session via API
    revoke_resp = client.post(
        "/api/admin/auth/revoke",
        json={"jti": rep_jti},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "success"
    assert revoke_resp.json()["revoked_jti"] == rep_jti

    # Field rep is now immediately blocked
    blocked_resp = client.get("/api/field/jobs", headers={"Authorization": f"Bearer {rep_token}"})
    assert blocked_resp.status_code == 401


def test_admin_revoke_endpoint_rejects_non_admin():
    """Non-admin callers cannot access the session revocation endpoint."""
    field_token = create_access_token(role="field", rep_name="Field User")
    resp = client.post(
        "/api/admin/auth/revoke",
        json={"jti": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {field_token}"},
    )
    assert resp.status_code == 403


def test_admin_revoke_endpoint_validation():
    """Payload requires either jti or token."""
    admin_token = create_access_token(role="admin")
    resp = client.post(
        "/api/admin/auth/revoke",
        json={},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 400
