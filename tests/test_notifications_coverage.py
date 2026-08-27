import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.notifications import RobustConnectionManager

@pytest.mark.asyncio
async def test_robust_connection_manager_coverage():
    """Test RobustConnectionManager connection, disconnect, broadcast, and heartbeat."""
    manager = RobustConnectionManager()

    # Create mock websockets
    ws1 = AsyncMock()
    ws2 = AsyncMock()

    # Connect
    await manager.connect(ws1, client_id="c1", role="admin")
    await manager.connect(ws2, client_id="c2", role="field")

    assert len(manager.active_connections) == 2
    assert manager.active_connections[ws1]["client_id"] == "c1"

    # Broadcast success
    await manager.broadcast({"data": "test"})
    ws1.send_json.assert_called_with({"data": "test"})
    ws2.send_json.assert_called_with({"data": "test"})

    # Broadcast error (simulate dead connection)
    ws1.send_json.side_effect = Exception("Connection closed")
    await manager.broadcast({"data": "error"})
    
    # ws1 should be disconnected
    assert len(manager.active_connections) == 1
    assert ws1 not in manager.active_connections

    # update_pong
    manager.update_pong(ws2)
    assert manager.active_connections[ws2]["last_pong"] > 0

    # disconnect
    manager.disconnect(ws2)
    assert len(manager.active_connections) == 0

    # Heartbeat loop culling zombie connections
    ws3 = AsyncMock()
    await manager.connect(ws3, client_id="c3", role="operations")
    # Simulate zombie: set last_pong to > 90 seconds ago
    manager.active_connections[ws3]["last_pong"] = time.time() - 100

    # Cancel the background task created by connect() so we can test the loop cleanly
    if manager._heartbeat_task:
        manager._heartbeat_task.cancel()
        try:
            await manager._heartbeat_task
        except asyncio.CancelledError:
            pass

    # Mock asyncio.sleep to break the infinite loop after 1 run, using original_sleep to avoid recursion
    original_sleep = asyncio.sleep
    sleep_count = 0
    async def mock_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > 1:
            raise asyncio.CancelledError()
        await original_sleep(0.001)

    with patch("asyncio.sleep", side_effect=mock_sleep):
        try:
            await manager._heartbeat_loop()
        except asyncio.CancelledError:
            pass

    # ws3 should have been culled
    assert ws3 not in manager.active_connections
    ws3.close.assert_called_once()
