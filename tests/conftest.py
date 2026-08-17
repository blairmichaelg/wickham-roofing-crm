import os

import pytest

os.environ["ADMIN_PIN"] = "9999"
os.environ["ACCOUNTING_PIN"] = "8888"
os.environ["OPERATIONS_PIN"] = "7777"
os.environ["APP_ENV"] = "test"

from app.core.database import run_migrations as init_db


@pytest.fixture(autouse=True, scope="session")
def setup_test_db(tmp_path_factory):
    """
    Ensure the database is initialized with all tables (including pricing)
    for the test suite. We just point the DB_PATH to a temp file.
    """
    from app.config import get_settings
    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()

    test_db = tmp_path_factory.mktemp("db") / "test_truck_server.db"
    
    import app.core.database
    app.core.database.get_db_path = lambda: test_db
    
    # Initialize schema
    init_db()

    # Seed default field rep with PIN 1111 for automated tests
    conn = app.core.database.get_connection()
    try:
        from passlib.context import CryptContext
        pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
        conn.execute(
            "INSERT OR IGNORE INTO field_reps (id, name, pin_hash, is_active) VALUES (?, ?, ?, 1)",
            ("00000000-0000-0000-0000-000000000001", "Test Field Rep", pwd_ctx.hash("1111"))
        )
        conn.commit()
    finally:
        conn.close()
    
    yield

@pytest.fixture(autouse=True)
def clear_rate_limits():
    """Clear rate limits before every test."""
    try:
        from app.services.rate_limit import _request_history
        _request_history.clear()
    except ImportError:
        pass

@pytest.fixture(autouse=True)
def patch_pipeline_writebacks(monkeypatch):
    """
    Patch DB writeback helpers to no-ops in all tests.
    Tests that specifically test writeback behavior should
    override this fixture or use the real function directly.
    """
    monkeypatch.setattr(
        "app.core.pipeline._writeback_sol_financials",
        lambda conn, job_id, sol_data: None
    )
    monkeypatch.setattr(
        "app.core.pipeline._writeback_ev_geometry",
        lambda conn, job_id, ev_data: None
    )
    monkeypatch.setattr(
        "app.core.pipeline._writeback_sol_claim_info",
        lambda conn, job_id, sol_data: None
    )
