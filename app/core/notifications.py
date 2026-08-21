import asyncio
import time
from typing import Any

import structlog
from fastapi import WebSocket

logger = structlog.get_logger("app.core.notifications")

class RobustConnectionManager:
    """RobustConnectionManager definition."""
    def __init__(self) -> None:
        # Maps websocket -> dict with 'client_id', 'role', 'last_pong'
        self.active_connections: dict[WebSocket, dict[str, Any]] = {}
        # Start the loop lazily on the first connection
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def connect(self, websocket: WebSocket, client_id: str = "unknown", role: str = "unknown") -> None:
        """
        Connect functionality.
        
        Args:
                websocket (WebSocket): websocket parameter.
                client_id (str): client_id parameter.
                role (str): role parameter.
        
        Returns:
            Any: The resulting output.
        """
        await websocket.accept()
        self.active_connections[websocket] = {
            "client_id": client_id,
            "role": role,
            "last_pong": time.time()
        }
        logger.info("websocket_client_connected", client_id=client_id, role=role, active_count=len(self.active_connections))
        
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Disconnect functionality.
        
        Args:
                websocket (WebSocket): websocket parameter.
        
        Returns:
            Any: The resulting output.
        """
        if websocket in self.active_connections:
            meta = self.active_connections.pop(websocket)
            logger.info("websocket_client_disconnected", client_id=meta["client_id"], active_count=len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """
        Broadcast functionality.
        
        Args:
                message (dict): message parameter.
        
        Returns:
            Any: The resulting output.
        """
        dead_connections = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning("websocket_broadcast_failed", error=str(e))
                dead_connections.add(connection)
                
        # Clean up any dead connections
        for dead_conn in dead_connections:
            self.disconnect(dead_conn)

    def update_pong(self, websocket: WebSocket) -> None:
        """Update the last_pong timestamp when a pong is received from the client."""
        if websocket in self.active_connections:
            self.active_connections[websocket]["last_pong"] = time.time()

    async def _heartbeat_loop(self) -> None:
        """Background loop to ping connections and disconnect zombies."""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            zombies = set()
            
            # Iterate over a list of items to avoid dict size changing during iteration
            for ws, meta in list(self.active_connections.items()):
                if now - meta["last_pong"] > 90:
                    zombies.add(ws)
                else:
                    try:
                        await ws.send_json({"type": "ping", "timestamp": now})
                    except Exception:
                        zombies.add(ws)
                        
            for zombie in zombies:
                if zombie in self.active_connections:
                    logger.warning("websocket_zombie_culled", client_id=self.active_connections[zombie]["client_id"])
                    self.disconnect(zombie)
                    try:
                        await zombie.close()
                    except Exception as e:
                        logger.error("websocket_zombie_close_failed", error=str(e))

# Global singleton
notifier = RobustConnectionManager()
