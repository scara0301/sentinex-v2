import json
import time
import uuid
import asyncio
from datetime import datetime, timezone
from mitmproxy import http, ctx as mctx
import redis.asyncio as aioredis
import structlog

from sentinex_core.events.schema import (
    EventEnvelope, ToolCallPayload, ToolResultPayload
)
from .settings import proxy_settings
from .interceptors.http import classify_request, classify_response
from .chain_tracker import ChainTracker

log = structlog.get_logger()

class SentinexAddon:
    def __init__(self):
        self.redis: aioredis.Redis = None
        self.seq = 0
        self.chain_tracker = ChainTracker()
        self._pending: dict[str, tuple[float, EventEnvelope]] = {}

    def running(self):
        import asyncio
        loop = asyncio.get_event_loop()
        self.redis = aioredis.from_url(proxy_settings.redis_url)
        log.info("Proxy addon running", scan_id=proxy_settings.scan_id)

    def request(self, flow: http.HTTPFlow) -> None:
        classification = classify_request(flow)
        if not classification:
            return  # pass through unrecognized traffic

        chain_id = str(uuid.uuid4())
        flow.metadata["chain_id"] = chain_id
        flow.metadata["req_ts"] = time.monotonic()

        event = EventEnvelope(
            scan_id=uuid.UUID(proxy_settings.scan_id) if proxy_settings.scan_id != "unknown" else uuid.uuid4(),
            seq=self._next_seq(),
            ts=datetime.now(timezone.utc),
            type="tool_call",
            payload=ToolCallPayload(
                agent_id="unknown",
                tool=classification["tool"],
                args=classification["args"],
                transport="http",
                chain_id=chain_id,
                host=classification.get("host"),
            ),
        )
        self._pending[chain_id] = (time.monotonic(), event)
        self._publish_sync(event)

    def response(self, flow: http.HTTPFlow) -> None:
        chain_id = flow.metadata.get("chain_id")
        if not chain_id:
            return

        req_ts, req_event = self._pending.pop(chain_id, (None, None))
        duration_ms = int((time.monotonic() - (req_ts or time.monotonic())) * 1000)

        classification = classify_response(flow)
        event = EventEnvelope(
            scan_id=req_event.scan_id if req_event else uuid.UUID(proxy_settings.scan_id) if proxy_settings.scan_id != "unknown" else uuid.uuid4(),
            seq=self._next_seq(),
            ts=datetime.now(timezone.utc),
            type="tool_result",
            payload=ToolResultPayload(
                chain_id=chain_id,
                ok=flow.response.status_code < 400,
                duration_ms=duration_ms,
                response=classification.get("response", {}),
                injected=False,
            ),
        )
        self._publish_sync(event)
        self.chain_tracker.record_call(chain_id, req_event, event)

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _publish_sync(self, event: EventEnvelope) -> None:
        """Publish to Redis synchronously (mitmproxy hooks are sync in v10)."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._publish(event))
            else:
                loop.run_until_complete(self._publish(event))
        except Exception as e:
            log.warning("Failed to publish event", error=str(e))

    async def _publish(self, event: EventEnvelope) -> None:
        if self.redis:
            channel = f"scan:{event.scan_id}:events"
            await self.redis.publish(channel, event.model_dump_json())


addons = [SentinexAddon()]
