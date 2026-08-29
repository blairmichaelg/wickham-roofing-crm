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


def save_push_subscription(user_id: str | None, role: str, endpoint: str, p256dh: str, auth: str) -> str:
    """Save or update a Web Push subscription in SQLite."""
    import uuid

    from app.core.database import get_connection

    conn = get_connection()
    try:
        sub_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO push_subscriptions (id, user_id, role, endpoint, p256dh_key, auth_key, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'utc'), datetime('now', 'utc'))
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                role = excluded.role,
                p256dh_key = excluded.p256dh_key,
                auth_key = excluded.auth_key,
                last_used_at = datetime('now', 'utc')
            """,
            (sub_id, user_id, role, endpoint, p256dh, auth)
        )
        conn.commit()
        return sub_id
    finally:
        conn.close()


def prune_dead_subscription(endpoint: str) -> None:
    """Remove an expired or invalid push subscription (HTTP 404 / 410)."""
    from app.core.database import get_connection

    conn = get_connection()
    try:
        conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        conn.commit()
        logger.info("push_subscription_pruned", endpoint=endpoint)
    finally:
        conn.close()


def dispatch_web_push(title: str, body: str, data: dict[str, Any] | None = None, role: str | None = None) -> dict[str, int]:
    """
    Dispatch Web Push notification to active push subscriptions matching role (or all if role is None).
    Prunes expired subscriptions (HTTP 404/410).
    """
    import json
    import os

    from app.core.database import get_connection

    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY")
    vapid_claim_email = os.getenv("VAPID_CLAIM_EMAIL", "mailto:admin@wickhamroofing.com")

    if not vapid_private_key:
        logger.warning("webpush_skipped_no_vapid_key")
        return {"sent": 0, "failed": 0, "pruned": 0}

    conn = get_connection()
    try:
        if role:
            cursor = conn.execute(
                "SELECT endpoint, p256dh_key, auth_key FROM push_subscriptions WHERE role = ?",
                (role,)
            )
        else:
            cursor = conn.execute(
                "SELECT endpoint, p256dh_key, auth_key FROM push_subscriptions"
            )
        subs = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    if not subs:
        return {"sent": 0, "failed": 0, "pruned": 0}

    payload = json.dumps({
        "title": title,
        "body": body,
        "data": data or {}
    })

    from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]

    sent = 0
    failed = 0
    pruned = 0

    for sub in subs:
        endpoint = sub["endpoint"]
        sub_info = {
            "endpoint": endpoint,
            "keys": {
                "p256dh": sub["p256dh_key"],
                "auth": sub["auth_key"]
            }
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims={"sub": vapid_claim_email}
            )
            sent += 1
        except WebPushException as ex:
            response = getattr(ex, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (404, 410):
                prune_dead_subscription(endpoint)
                pruned += 1
            else:
                logger.error("webpush_send_error", endpoint=endpoint, error=str(ex))
                failed += 1
        except Exception as e:
            logger.error("webpush_send_unexpected_error", endpoint=endpoint, error=str(e))
            failed += 1

    return {"sent": sent, "failed": failed, "pruned": pruned}

