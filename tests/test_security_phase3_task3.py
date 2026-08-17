from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.main import app

client = TestClient(app)

def test_accounting_cannot_access_admin_route():
    """Task 3: Test that accounting token is rejected by admin route."""
    # Create an accounting token
    token = create_access_token(role="accounting")
    client.cookies.set("auth_token", token)
    
    # Try to access an admin-only route in office_routes.py
    # /api/office/jobs requires verify_admin
    response = client.get("/api/office/jobs")
    assert response.status_code == 403
    assert response.json() == {"detail": "Not authorized for admin access"}

def test_accounting_can_access_accounting_route():
    """Task 3: Test that accounting token can access accounting route."""
    # Create an accounting token
    token = create_access_token(role="accounting")
    client.cookies.set("auth_token", token)
    
    # Try to access an accounting route in office_routes.py
    # /api/office/accounting/brief requires verify_accounting
    response = client.get("/api/office/accounting/brief")
    assert response.status_code == 200
