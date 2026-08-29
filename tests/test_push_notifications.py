import json
import uuid
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from pywebpush import WebPushException

from app.api.auth import create_access_token
from app.core.database import get_connection, update_job_status
from app.core.notifications import (
    dispatch_web_push,
    prune_dead_subscription,
    save_push_subscription,
)
from app.main import app


def test_save_and_prune_push_subscription():
    user_id = str(uuid.uuid4())
    endpoint = f"https://updates.push.services.mozilla.com/wpush/v2/{uuid.uuid4()}"
    p256dh = "BCVxsr7N_eNg6gDbWYnZsOEvC6"
    auth = "tBHItJI5svbpez7KI4CCXg"

    # 1. Save subscription
    sub_id = save_push_subscription(user_id, "crew", endpoint, p256dh, auth)
    assert sub_id is not None

    conn = get_connection()
    row = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
    conn.close()
    assert row is not None
    assert row["user_id"] == user_id
    assert row["role"] == "crew"
    assert row["p256dh_key"] == p256dh
    assert row["auth_key"] == auth

    # 2. Prune subscription
    prune_dead_subscription(endpoint)

    conn = get_connection()
    row_after = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
    conn.close()
    assert row_after is None


def test_api_field_push_subscribe():
    client = TestClient(app)
    field_token = create_access_token("field")
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4()}"

    payload = {
        "endpoint": endpoint,
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QT9Ac",
            "auth": "tBHI_test_auth_key"
        },
        "role": "crew"
    }

    try:
        res = client.post(
            "/api/field/push/subscribe",
            json=payload,
            cookies={"auth_token": field_token}
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == "success"
        assert "subscription_id" in data

        # Check DB
        conn = get_connection()
        row = conn.execute("SELECT role, p256dh_key FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        conn.close()
        assert row is not None
        assert row["role"] == "crew"
        assert row["p256dh_key"] == payload["keys"]["p256dh"]
    finally:
        prune_dead_subscription(endpoint)


def test_dispatch_web_push_success(monkeypatch):
    endpoint = f"https://push.example.com/{uuid.uuid4()}"
    save_push_subscription("user-1", "crew", endpoint, "key1", "auth1")

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "dummy_private_key")
    monkeypatch.setenv("VAPID_CLAIM_EMAIL", "mailto:test@wickham.com")

    webpush_mock = MagicMock()
    monkeypatch.setattr("pywebpush.webpush", webpush_mock)

    try:
        res = dispatch_web_push(
            title="Install Scheduled",
            body="Roof install scheduled for tomorrow.",
            data={"job_id": "123", "url": "/field/jobs/123"},
            role="crew"
        )
        assert res["sent"] == 1
        assert res["failed"] == 0
        assert res["pruned"] == 0
        webpush_mock.assert_called_once()
    finally:
        prune_dead_subscription(endpoint)


def test_dispatch_web_push_prunes_expired_410(monkeypatch):
    endpoint = f"https://push.example.com/{uuid.uuid4()}"
    save_push_subscription("user-2", "crew", endpoint, "key2", "auth2")

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "dummy_private_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 410
    webpush_exc = WebPushException("Gone", response=mock_resp)

    def mock_webpush(*args, **kwargs):
        raise webpush_exc

    monkeypatch.setattr("pywebpush.webpush", mock_webpush)

    try:
        res = dispatch_web_push(
            title="Alert",
            body="Job alert",
            role="crew"
        )
        assert res["sent"] == 0
        assert res["pruned"] == 1

        # Check DB to confirm pruned
        conn = get_connection()
        row = conn.execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        conn.close()
        assert row is None
    finally:
        prune_dead_subscription(endpoint)


def test_install_scheduled_triggers_push(monkeypatch):
    job_id = str(uuid.uuid4())
    conn = get_connection()
    conn.execute(
        "INSERT INTO jobs (id, homeowner_name, address_line1, city, state, postal_code, phone, status, job_type) VALUES (?, 'Push Homeowner', '500 Scheduled St', 'Valdosta', 'GA', '31601', '555-0150', 'MATERIALS_ON_SITE', 'INSURANCE')",
        (job_id,)
    )
    conn.commit()
    conn.close()

    dispatch_called = False

    def mock_dispatch(title, body, data=None, role=None):
        nonlocal dispatch_called
        dispatch_called = True
        assert "Push Homeowner" in title
        assert "500 Scheduled St" in body
        assert role == "crew"
        return {"sent": 1, "failed": 0, "pruned": 0}

    monkeypatch.setattr("app.core.notifications.dispatch_web_push", mock_dispatch)

    try:
        update_job_status(job_id, "INSTALL_SCHEDULED", "Installation crew assigned")
        assert dispatch_called is True
    finally:
        conn = get_connection()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.commit()
        conn.close()


def test_dispatch_web_push_role_filtering_excludes_field_subscribers(monkeypatch):
    crew_endpoint = f"https://push.example.com/crew/{uuid.uuid4()}"
    field_endpoint = f"https://push.example.com/field/{uuid.uuid4()}"

    save_push_subscription("user-crew", "crew", crew_endpoint, "key_crew", "auth_crew")
    save_push_subscription("user-field", "field", field_endpoint, "key_field", "auth_field")

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "dummy_private_key")

    called_endpoints = []

    def mock_webpush(subscription_info, *args, **kwargs):
        called_endpoints.append(subscription_info["endpoint"])

    monkeypatch.setattr("pywebpush.webpush", mock_webpush)

    try:
        # When dispatching to role="crew", only crew subscriber receives notification
        res = dispatch_web_push(
            title="Crew Alert",
            body="New crew schedule",
            role="crew"
        )
        assert res["sent"] == 1
        assert crew_endpoint in called_endpoints
        assert field_endpoint not in called_endpoints
    finally:
        prune_dead_subscription(crew_endpoint)
        prune_dead_subscription(field_endpoint)

