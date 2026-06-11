"""
WebSocket endpoint for real-time scan event streaming.

Protocol
--------
1. Client connects to ``WS /workspace/{wid}/scan/{sid}/live``
2. Client may send ``{"resume_from": <last_seq>}`` to replay missed
   events from the database, then seamlessly switch to live.
3. Server streams ``EventEnvelope`` JSON objects in real-time.
4. Client may send ``{"type": "ping"}`` for keepalive; server responds
   with ``{"type": "pong"}``.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import structlog
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from sentinex_core.db.base import get_session
from sentinex_core.db.repos import EventRepo, ScanRepo, WorkspaceRepo

log = structlog.get_logger()

router = APIRouter()

# Browsers can't set custom headers on a WebSocket, but they can offer
# subprotocols. The dashboard sends ["sentinex-api-key", "<key>"] so the key
# stays out of the URL and access logs; we fall back to the api_key query
# param for non-browser clients (curl/wscat).
_API_KEY_SUBPROTOCOL = "sentinex-api-key"


def _resolve_api_key(
    websocket: WebSocket, query_api_key: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """Return (api_key, negotiated_subprotocol)."""
    offered = list(websocket.scope.get("subprotocols", []) or [])
    if len(offered) >= 2 and offered[0] == _API_KEY_SUBPROTOCOL:
        return offered[1], _API_KEY_SUBPROTOCOL
    return query_api_key, None


@router.websocket("/workspace/{workspace_id}/scan/{scan_id}/live")
async def scan_live_ws(
    websocket: WebSocket,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    api_key: Optional[str] = Query(None),
):
    manager = websocket.app.state.ws_manager
    fanout = websocket.app.state.ws_fanout
    scan_key = str(scan_id)

    resolved_key, subprotocol = _resolve_api_key(websocket, api_key)
    if not resolved_key:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    key_hash = hashlib.sha256(resolved_key.encode()).hexdigest()
    async with get_session() as db:
        ws_repo = WorkspaceRepo(db)
        caller_workspace = await ws_repo.get_by_api_key(key_hash)
    if not caller_workspace or caller_workspace.id != workspace_id:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    async with get_session() as db:
        scan_repo = ScanRepo(db)
        scan = await scan_repo.get_by_id(scan_id)
        if not scan or scan.workspace_id != workspace_id:
            await websocket.close(code=4004, reason="Scan not found")
            return

    await manager.connect(scan_key, websocket, subprotocol=subprotocol)
    await fanout.subscribe(scan_key)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "invalid JSON"})
                continue

            if "resume_from" in msg:
                from_seq = int(msg["resume_from"])
                limit = min(int(msg.get("limit", 500)), 1000)
                async with get_session() as db:
                    event_repo = EventRepo(db)
                    events = await event_repo.list_by_scan(
                        scan_id, from_seq=from_seq, limit=limit,
                    )
                    for ev in events:
                        await websocket.send_json({
                            "scan_id": str(ev.scan_id),
                            "seq": ev.seq,
                            "ts": ev.ts.isoformat() if ev.ts else None,
                            "type": ev.type,
                            "payload": ev.payload,
                            "_replay": True,
                        })
                    last = events[-1].seq if events else from_seq
                    await websocket.send_json({
                        "_replay_done": True,
                        "last_seq": last,
                        "count": len(events),
                    })

            elif msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        log.info("ws_client_disconnected", scan_id=scan_key)
    except Exception as exc:
        log.warning("ws_error", scan_id=scan_key, error=str(exc))
    finally:
        manager.disconnect(scan_key, websocket)
        await fanout.unsubscribe(scan_key)
