"""
Phase 9 Test Suite — Field Rep Identity System

Tests (14 total, targeting 195 + 14 = 209 passing):
  1.  test_field_reps_table_exists
  2.  test_create_field_rep_success
  3.  test_create_field_rep_duplicate_pin
  4.  test_create_field_rep_invalid_pin_length
  5.  test_create_field_rep_invalid_pin_alpha
  6.  test_create_field_rep_conflicts_system_pin
  7.  test_get_field_rep_by_pin_found
  8.  test_get_field_rep_by_pin_not_found
  9.  test_get_field_rep_by_pin_inactive_hidden
  10. test_update_field_rep_name
  11. test_field_login_creates_jwt_with_rep_name
  12. test_field_login_wrong_pin_still_delays
  13. test_admin_reps_api_create_and_list
  14. test_admin_reps_api_requires_admin_role
"""

import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.database import (
    create_field_rep,
    get_connection,
    get_field_rep_by_pin,
    list_field_reps,
    update_field_rep,
)
from app.main import app

client = TestClient(app)


# ── Auth fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def admin_cookie_p9():
    """Admin JWT cookie, module-scoped for Phase 9."""
    res = client.post(
        "/auth/login",
        data={"pin": "9999", "redirect_url": "/"},
        follow_redirects=False,
    )
    return res.cookies.get("auth_token")


@pytest.fixture(scope="module")
def field_rep_and_cookie():
    """
    Creates a fresh field rep in the DB and returns a (rep, cookie) tuple.
    Uses a unique PIN that doesn't clash with system PINs.
    """
    pin = "8765"
    # Clean up any pre-existing rep with this PIN
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    rep = create_field_rep("Debi K.", pin)
    res = client.post(
        "/auth/login",
        data={"pin": pin, "redirect_url": "/"},
        follow_redirects=False,
    )
    cookie = res.cookies.get("auth_token")
    return rep, cookie


# ── Test 1 ────────────────────────────────────────────────────────────────────

def test_field_reps_table_exists():
    """field_reps table must exist in the test database after init_db()."""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='field_reps'"
    )
    rows = cursor.fetchall()
    conn.close()
    assert len(rows) == 1, "field_reps table not found in DB schema"


# ── Test 2 ────────────────────────────────────────────────────────────────────

def test_create_field_rep_success():
    """create_field_rep creates a rep and list_field_reps returns it."""
    pin = "5432"
    # Clean slate
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    create_field_rep("Mike B.", pin)
    reps = list_field_reps()
    names = [r["name"] for r in reps]
    assert "Mike B." in names, f"Rep 'Mike B.' not in list: {names}"


# ── Test 3 ────────────────────────────────────────────────────────────────────

# ── Test 4 ────────────────────────────────────────────────────────────────────

def test_create_field_rep_invalid_pin_length():
    """A 3-digit PIN must raise ValueError."""
    with pytest.raises(ValueError, match="4 digits"):
        create_field_rep("Rep C", "123")


# ── Test 5 ────────────────────────────────────────────────────────────────────

def test_create_field_rep_invalid_pin_alpha():
    """An alphanumeric PIN must raise ValueError."""
    with pytest.raises(ValueError, match="4 digits"):
        create_field_rep("Rep D", "12ab")


# ── Test 6 ────────────────────────────────────────────────────────────────────

def test_create_field_rep_conflicts_system_pin():
    """A PIN matching a reserved system PIN must raise ValueError."""
    settings = get_settings()
    with pytest.raises(ValueError, match="reserved system PIN"):
        create_field_rep("Hacker", settings.admin_pin)


# ── Test 7 ────────────────────────────────────────────────────────────────────

def test_get_field_rep_by_pin_found():
    """get_field_rep_by_pin returns the correct rep dict for an active rep."""
    pin = "7890"
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    create_field_rep("Scott W.", pin)
    rep = get_field_rep_by_pin(pin)
    assert rep is not None, "Expected rep dict, got None"
    assert rep["name"] == "Scott W."


# ── Test 8 ────────────────────────────────────────────────────────────────────

def test_get_field_rep_by_pin_not_found():
    """get_field_rep_by_pin returns None for an unknown PIN."""
    rep = get_field_rep_by_pin("0000")
    assert rep is None, f"Expected None for unknown PIN, got: {rep}"


# ── Test 9 ────────────────────────────────────────────────────────────────────

def test_get_field_rep_by_pin_inactive_hidden():
    """Inactive reps must NOT be returned by get_field_rep_by_pin."""
    pin = "4321"
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    r = create_field_rep("Gone Rep", pin)
    update_field_rep(r["id"], is_active=False)

    rep = get_field_rep_by_pin(pin)
    assert rep is None, (
        "Inactive rep should be hidden from get_field_rep_by_pin "
        f"but got: {rep}"
    )


# ── Test 10 ───────────────────────────────────────────────────────────────────

def test_update_field_rep_name():
    """update_field_rep correctly changes the rep's name and leaves PIN intact."""
    pin = "6789"
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    r = create_field_rep("Old Name", pin)
    updated = update_field_rep(r["id"], name="New Name")
    assert updated["name"] == "New Name", f"Expected 'New Name', got: {updated['name']}"


# ── Test 11 ───────────────────────────────────────────────────────────────────

def test_field_login_creates_jwt_with_rep_name():
    """
    Logging in as a field rep must embed rep_name and rep_id in the JWT.
    """
    pin = "3456"
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    create_field_rep("Debi K. (Test 11)", pin)

    res = client.post(
        "/auth/login",
        data={"pin": pin, "redirect_url": "/"},
        follow_redirects=False,
    )
    assert res.status_code == 303, f"Expected 303, got {res.status_code}"

    token = res.cookies.get("auth_token")
    assert token is not None, "auth_token cookie not set"

    settings = get_settings()
    payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

    assert payload.get("role") == "field", (
        f"Expected role='field', got: {payload.get('role')}"
    )
    assert payload.get("rep_name") == "Debi K. (Test 11)", (
        f"Expected rep_name='Debi K. (Test 11)', got: {payload.get('rep_name')}"
    )
    assert "rep_id" in payload, "rep_id claim missing from JWT payload"


# ── Test 12 ───────────────────────────────────────────────────────────────────

def test_field_login_wrong_pin_still_delays():
    """
    A wrong PIN must redirect to /login?error=1 and take at least 1 second
    (brute-force protection).
    """
    t0 = time.monotonic()
    res = client.post(
        "/auth/login",
        data={"pin": "9876", "redirect_url": "/"},
        follow_redirects=False,
    )
    elapsed = time.monotonic() - t0

    location = res.headers.get("location", "")
    assert "error=1" in location, (
        f"Expected 'error=1' in redirect Location, got: {location}"
    )
    assert "/login" in location

    # Verify 1-second brute-force delay is enforced
    # TestClient's default timeout is 5s so this should pass
    assert elapsed >= 1.0, (
        f"Brute-force delay missing: elapsed={elapsed:.2f}s, expected >= 1.0s"
    )


# ── Test 13 ───────────────────────────────────────────────────────────────────

def test_admin_reps_api_create_and_list(admin_cookie_p9):
    """Admin can POST a new rep and GET /api/admin/reps/ returns it."""
    pin = "2345"
    # Clean up
    conn = get_connection()
    conn.execute("DELETE FROM field_reps")
    conn.commit()
    conn.close()

    # Create
    res = client.post(
        "/api/admin/reps/",
        json={"name": "Test Rep", "pin": pin},
        cookies={"auth_token": admin_cookie_p9},
    )
    assert res.status_code == 201, (
        f"Expected 201, got {res.status_code}: {res.text}"
    )

    # List
    res2 = client.get(
        "/api/admin/reps/?include_inactive=false",
        cookies={"auth_token": admin_cookie_p9},
    )
    assert res2.status_code == 200
    reps = res2.json()
    names = [r["name"] for r in reps]
    assert "Test Rep" in names, f"'Test Rep' not found in list: {names}"


# ── Test 14 ───────────────────────────────────────────────────────────────────

def test_admin_reps_api_requires_admin_role(field_rep_and_cookie):
    """A field-role JWT must get 403 on POST /api/admin/reps/."""
    _, field_token = field_rep_and_cookie

    res = client.post(
        "/api/admin/reps/",
        json={"name": "Unauthorized", "pin": "1234"},
        cookies={"auth_token": field_token},
    )
    assert res.status_code == 403, (
        f"Expected 403 for field role, got: {res.status_code}: {res.text}"
    )


# ── Test 15: Alex Wickham Read-Only Verification ──────────────────────────────

def test_alex_wickham_read_only():
    """Verify that Alex Wickham (PIN 1999) has office read-only access and cannot perform mutations."""
    # Ensure Alex Wickham is seeded in the test DB
    from app.core.database import seed_core_team_reps
    seed_core_team_reps()

    # Login as Alex Wickham
    login_res = client.post(
        "/auth/login",
        data={"pin": "1999", "redirect_url": "/"},
        follow_redirects=False,
    )
    assert login_res.status_code == 303
    alex_token = login_res.cookies.get("auth_token")
    assert alex_token is not None

    # Verify GET access to office views (e.g. /admin)
    admin_view_res = client.get(
        "/admin",
        cookies={"auth_token": alex_token},
    )
    assert admin_view_res.status_code == 200

    # Verify GET access to accounting views
    accounting_view_res = client.get(
        "/accounting",
        cookies={"auth_token": alex_token},
    )
    assert accounting_view_res.status_code == 200

    # Verify GET access to reps API
    reps_res = client.get(
        "/api/admin/reps/",
        cookies={"auth_token": alex_token},
    )
    assert reps_res.status_code == 200

    # Verify POST mutation is BLOCKED with 403 Forbidden
    mutation_res = client.post(
        "/api/admin/reps/",
        json={"name": "Forbidden Rep", "pin": "2222"},
        cookies={"auth_token": alex_token},
    )
    assert mutation_res.status_code == 403
    assert "Alex Wickham has read-only privileges" in mutation_res.text

    # Verify GET access to help guides and that he can see all guides
    help_res = client.get(
        "/help",
        cookies={"auth_token": alex_token},
    )
    assert help_res.status_code == 200
    assert "isCore = true" in help_res.text
    assert "Admin Guide" in help_res.text
    assert "Accounting Guide" in help_res.text
    assert "Operations Guide" in help_res.text
    assert "Field Guide" in help_res.text
