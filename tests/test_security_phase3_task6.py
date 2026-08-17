from fastapi.testclient import TestClient

from app.api.auth import create_access_token
from app.main import app
from app.services.rate_limit import reset_rate_limits

client = TestClient(app, raise_server_exceptions=False)

def test_rate_limit_sliding_window():
    """Task 6: Test that rate limiter rejects bursts over 3 requests per 10s."""
    reset_rate_limits()
    
    token = create_access_token(role="admin")
    client.cookies.set("auth_token", token)
    
    # Fire 4 requests quickly
    responses = []
    for _ in range(4):
        # We can hit the generate_material_order endpoint to trigger the check.
        # It might fail with 422 or 400 later in the body, but 429 should happen first.
        # But let's send an empty payload to see if it gets past dependency check.
        # Wait, if we send an empty payload, it will 422 Unprocessable Entity BEFORE rate limit?
        # No, Depends(check_rate_limit) runs before body validation if it's in the route dependencies!
        # Let's test hitting /api/office/jobs/test_job/escalate which has no body.
        response = client.post("/api/office/jobs/test_job/escalate")
        responses.append(response.status_code)
    
    # Since the queue_escalation doesn't have a body, it should return 500 (Redis not set up in tests) 
    # or something else, but one MUST return 429.
    # Actually, we don't care about 500. We just care that the 4th request returns 429.
    assert responses.count(429) == 1
    
    # Wait, in the test client, the IP is the same for all requests.
    # The first 3 should be allowed (status != 429), the 4th should be 429.
    assert responses[3] == 429
