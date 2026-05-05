"""
WebSocket connection manager — per-scan room registry.

Maintains a mapping of scan_id → set[WebSocket] so that events are only
fanned out to clients watching that particular scan.  All operations are
safe within a single asyncio event loop (FastAPI's default).
"""

from __future__ import annotations

import structlog
from fastapi import WebSocket

log = structlog.get_logger()


class ConnectionManager:
    """Per-scan WebSocket room registry."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}

    async def connect(self, scan_id: str, ws: WebSocket) -> None:
        """Accept the WebSocket and add it to the scan's room."""
        await ws.accept()
        room = self._rooms.setdefault(scan_id, set())
        room.add(ws)
        log.info("ws_connect", scan_id=scan_id, clients=len(room))

    def disconnect(self, scan_id: str, ws: WebSocket) -> None:
        """Remove a WebSocket from its room; clean up empty rooms."""
        room = self._rooms.get(scan_id)
        if room:
            room.discard(ws)
            if not room:
                del self._rooms[scan_id]
        remaining = len(self._rooms.get(scan_id, set()))
        log.info("ws_disconnect", scan_id=scan_id, remaining=remaining)

    async def broadcast(self, scan_id: str, message: str) -> None:
        """Send a text message to every client in a scan room."""
        room = self._rooms.get(scan_id)
        if not room:
            return
        dead: list[WebSocket] = []
        for ws in room:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            room.discard(ws)
            log.debug("ws_dead_connection_removed", scan_id=scan_id)

    def room_size(self, scan_id: str) -> int:
        """Number of active clients watching a scan."""
        return len(self._rooms.get(scan_id, set()))

    def active_scans(self) -> list[str]:
        """List scan IDs that have at least one connected client."""
        return list(self._rooms.keys())
