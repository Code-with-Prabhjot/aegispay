"""
backend/app/services/broadcaster.py
Real-Time WebSocket Connection Manager & Ledger Event Broadcaster for AegisPay.

Replaces 2-second HTTP polling with a push-based WebSocket streaming pipeline
for AG Grid Mission Control. Broadcasts standard envelope events:
- FSM_TRANSITION: FSM state advances (e.g. PAYPAL_AUTH_LOCKED -> ARBITRATION_AUDITING)
- LEDGER_TRANSACTION: Double-entry journal entries posted (debits == credits)
- ARBITER_EVALUATED: Verification arbiter score, AST audit, and settlement split calculated
- INITIAL_SNAPSHOT: Sent immediately upon connection to bootstrap client state
- HEARTBEAT: Periodic ping/pong to prevent proxy timeouts
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


def format_event_envelope(
    event_type: str,
    agreement_id: str,
    data: Dict[str, Any],
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Formats payload into the standard AegisPay event envelope."""
    return {
        "event_type": event_type,
        "agreement_id": agreement_id,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


class LedgerConnectionManager:
    """
    Thread-safe WebSocket connection manager and real-time event broadcaster.
    Maintains active connections, dispatches non-blocking broadcasts,
    and manages keepalive heartbeats.
    """

    def __init__(self, heartbeat_interval_seconds: float = 30.0) -> None:
        self._active_connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.heartbeat_interval = heartbeat_interval_seconds

    @property
    def active_connections(self) -> List[WebSocket]:
        return list(self._active_connections)

    @property
    def active_count(self) -> int:
        return len(self._active_connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accepts the WebSocket connection and registers it in the active set."""
        await websocket.accept()
        async with self._lock:
            self._active_connections.add(websocket)
        logger.info("WebSocket client connected. Active connections: %d", len(self._active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Safely unregisters a WebSocket connection."""
        async with self._lock:
            self._active_connections.discard(websocket)
        logger.info("WebSocket client disconnected. Active connections: %d", len(self._active_connections))

    async def broadcast(self, event: Dict[str, Any]) -> None:
        """
        Broadcasts an event envelope to all connected WebSocket clients.
        Automatically purges stale or dropped connections.
        """
        async with self._lock:
            connections = list(self._active_connections)

        if not connections:
            return

        dead_connections: List[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_json(event)
            except Exception as exc:
                logger.debug("Failed sending to client %s: %s; scheduling cleanup", ws, exc)
                dead_connections.append(ws)

        if dead_connections:
            async with self._lock:
                for ws in dead_connections:
                    self._active_connections.discard(ws)

    def broadcast_sync(self, event: Dict[str, Any]) -> None:
        """
        Non-blocking dispatch using asyncio.create_task on the running event loop.
        Safe to call from synchronous code paths during async requests.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(event))
        except RuntimeError:
            # No running event loop in this context (e.g. standalone sync unit tests)
            pass

    async def handle_heartbeat(self, websocket: WebSocket, incoming_text: str) -> bool:
        """
        Responds to client ping messages with a pong heartbeat.
        Returns True if the message was a heartbeat ping, False otherwise.
        """
        cleaned = incoming_text.strip().lower()
        is_ping = cleaned == "ping" or '"ping"' in cleaned or '"type": "ping"' in cleaned
        if is_ping:
            pong_envelope = format_event_envelope(
                event_type="HEARTBEAT",
                agreement_id="SYSTEM",
                data={"type": "pong", "status": "alive"},
            )
            await websocket.send_json(pong_envelope)
            return True
        return False

    async def send_heartbeat_ping(self) -> None:
        """Proactively sends a ping to all connected clients to keep proxy connections alive."""
        ping_envelope = format_event_envelope(
            event_type="HEARTBEAT",
            agreement_id="SYSTEM",
            data={"type": "ping", "status": "keepalive"},
        )
        await self.broadcast(ping_envelope)


# Global default connection manager instance
ledger_broadcaster = LedgerConnectionManager()
