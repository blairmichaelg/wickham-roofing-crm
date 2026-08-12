"""
WebSocket Router: Real-time Office WebSocket feed.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.auth import decode_token
from app.core.notifications import notifier

router = APIRouter()
logger = structlog.get_logger("app.api.websockets")


@router.websocket("/ws/office")
async def office_ws(websocket: WebSocket):
    """
    WebSocket endpoint for office real-time notifications.
    """
    token = (
        websocket.query_params.get("token") or websocket.cookies.get("auth_token")
    )
    if not token:
        await websocket.close(
            code=1008, reason="Unauthorized: Missing authentication token"
        )
        return
    try:
        payload = decode_token(token)
        if payload.get("role") not in ["admin", "operations", "accounting"]:
            await websocket.close(
                code=1008, reason="Forbidden: Unauthorized role for office feed"
            )
            return
    except Exception:
        await websocket.close(code=1008, reason="Unauthorized: Invalid token")
        return

    await notifier.connect(
        websocket, client_id="office_client", role=payload.get("role", "office")
    )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "pong":
                notifier.update_pong(websocket)
    except WebSocketDisconnect:
        notifier.disconnect(websocket)
