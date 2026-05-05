"""
Redis pub/sub → WebSocket fanout bridge.

For each scan with at least one live WS client, a background task
subscribes to ``scan:{scan_id}:events`` and forwards every message to
the ConnectionManager.  Subscriptions are reference-counted: the first
WS client triggers subscribe; the last disconnect triggers unsubscribe.
"""

from __future__ import annotations

import asyncio
import structlog
import redis.asyncio as aioredis

from .manager import ConnectionManager

log = structlog.get_logger()


class RedisFanout:
    def __init__(self, redis_url: str, manager: ConnectionManager) -> None:
        self._manager = manager
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._tasks: dict[str, asyncio.Task] = {}
        self._ref_counts: dict[str, int] = {}

    async def subscribe(self, scan_id: str) -> None:
        """Increment ref count; start listener on first subscriber."""
        self._ref_counts[scan_id] = self._ref_counts.get(scan_id, 0) + 1
        if scan_id in self._tasks:
            return
        task = asyncio.create_task(self._listen(scan_id))
        self._tasks[scan_id] = task
        log.info("fanout_subscribe", scan_id=scan_id)

    async def unsubscribe(self, scan_id: str) -> None:
        """Decrement ref count; cancel listener when no subscribers remain."""
        count = self._ref_counts.get(scan_id, 0) - 1
        if count > 0:
            self._ref_counts[scan_id] = count
            return
        self._ref_counts.pop(scan_id, None)
        task = self._tasks.pop(scan_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log.info("fanout_unsubscribe", scan_id=scan_id)

    async def shutdown(self) -> None:
        """Cancel all listener tasks — called during app shutdown."""
        for scan_id in list(self._tasks):
            self._ref_counts[scan_id] = 0
            await self.unsubscribe(scan_id)
        await self._redis.aclose()

    async def _listen(self, scan_id: str) -> None:
        """Background loop: subscribe to Redis channel and fan out."""
        channel = f"scan:{scan_id}:events"
        pubsub = self._redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await self._manager.broadcast(scan_id, message["data"])
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.error("fanout_listen_error", scan_id=scan_id, error=str(exc))
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception as exc:
                log.debug("fanout_pubsub_close_error", scan_id=scan_id, error=str(exc))
