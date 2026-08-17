import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
response = client.post("/auth/login", data={"pin": "9999", "redirect_url": "/"}, follow_redirects=False)
auth_cookie = response.cookies.get("auth_token")
client.cookies.set("auth_token", auth_cookie)

def test_download_export_path_traversal():
    """Task 2: Test path traversal blocks on /api/office/download/{filename}"""
    # 1. Test ".." in a way that httpx doesn't normalize
    response = client.get("/api/office/download/my..file.csv")
    assert response.status_code == 400
    
    # 2. Test hidden file
    response = client.get("/api/office/download/.env")
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_download_export_legitimate(tmp_path):
    """Task 2: Test legitimate file download on /api/office/download/{filename}"""
    import os

    from app.api.office_routes import EXPORT_DIR
    
    # Create a legitimate file
    test_file = EXPORT_DIR / "legitimate_test_file.csv"
    test_file.write_text("id,name\n1,test")
    
    try:
        response = client.get("/api/office/download/legitimate_test_file.csv")
        assert response.status_code == 200
        assert "legitimate_test_file.csv" in response.headers.get("content-disposition", "")
    finally:
        if test_file.exists():
            os.remove(test_file)
