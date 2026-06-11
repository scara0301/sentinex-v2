"""
mitmproxy addon: records every tool call, reroutes provider hosts to mock
infrastructure, and applies scenario response injections.

NOTE: this file is loaded by ``mitmdump -s``, which imports it as a
top-level module — relative imports do not work here. The sentinex_proxy
package is installed system-wide in the proxy image, so absolute imports
resolve correctly.
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
import structlog
from mitmproxy import http

from sentinex_core.events.schema import (
    EventEnvelope,
    ToolCallPayload,
    ToolResultPayload,
)
from sentinex_core.findings import honeypots
from sentinex_proxy.chain_tracker import ChainTracker
from sentinex_proxy.injection import InjectionEngine
from sentinex_proxy.interceptors.http import classify_request, classify_response
from sentinex_proxy.settings import proxy_settings

log = structlog.get_logger()


class SentinexAddon:
    def __init__(self):
        self.redis: aioredis.Redis = None
        self.seq = 0  # local fallback when Redis is unreachable
        self.chain_tracker = ChainTracker()
        # Known planted values must be tracked from the first request — the
        # regex harvester only catches values that *look* like secrets.
        self.chain_tracker.seed_sensitive(honeypots.ALL)
        self.injector = InjectionEngine()
        self._pending: dict[str, tuple[float, EventEnvelope]] = {}
        # Breakpoint state (Sprint 5): while paused, tool responses are
        # intercepted (held) and released by resume/step control messages.
        self.paused = False
        self._intercepted: list[http.HTTPFlow] = []
        self._control_task = None
        try:
            self._mock_hosts: dict[str, str] = json.loads(
                proxy_settings.mock_host_map or "{}"
            )
        except ValueError:
            log.warning("Invalid MOCK_HOST_MAP JSON; mock routing disabled")
            self._mock_hosts = {}

    async def running(self):
        # mitmproxy hooks run on this asyncio loop, so a single async Redis
        # client serves both publishing and rule loading.
        self.redis = aioredis.from_url(proxy_settings.redis_url)
        await self._ensure_rules_loaded()
        try:
            self._control_task = asyncio.ensure_future(self._listen_control())
        except Exception as e:
            log.warning("Breakpoint control listener not started", error=str(e))
        log.info("Proxy addon running", scan_id=proxy_settings.scan_id)

    def done(self):
        if self._control_task is not None:
            self._control_task.cancel()

    async def request(self, flow: http.HTTPFlow) -> None:
        classification = classify_request(flow)
        if not classification:
            return  # pass through unrecognized traffic

        chain_id = str(uuid.uuid4())
        flow.metadata["chain_id"] = chain_id
        flow.metadata["req_ts"] = time.monotonic()
        flow.metadata["tool"] = classification["tool"]

        # Reroute provider hosts (api.stripe.com, slack.com, ...) to the
        # corresponding mock container on the sandbox network. The recorded
        # event keeps the *original* host so findings stay readable.
        self._route_to_mock(flow)

        event = EventEnvelope(
            scan_id=self._scan_uuid(),
            seq=await self._next_seq(),
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
        await self._publish(event)

    async def response(self, flow: http.HTTPFlow) -> None:
        chain_id = flow.metadata.get("chain_id")
        if not chain_id or flow.response is None:
            return

        req_ts, req_event = self._pending.pop(chain_id, (None, None))
        duration_ms = int((time.monotonic() - (req_ts or time.monotonic())) * 1000)

        # Late-load injection rules if Redis wasn't ready at startup.
        await self._ensure_rules_loaded()
        injected = self.injector.apply(flow, flow.metadata.get("tool"))

        classification = classify_response(flow)
        event = EventEnvelope(
            scan_id=req_event.scan_id if req_event else self._scan_uuid(),
            seq=await self._next_seq(),
            ts=datetime.now(timezone.utc),
            type="tool_result",
            payload=ToolResultPayload(
                chain_id=chain_id,
                ok=flow.response.status_code < 400,
                duration_ms=duration_ms,
                response=classification.get("response", {}),
                injected=injected,
            ),
        )
        await self._publish(event)
        # Maintains in-proxy call-chain state (sensitive-value harvesting, call
        # counts). Authoritative detection runs post-scan in ScenarioRunner over
        # the persisted event stream, so the returned rule hints are advisory
        # and intentionally not acted on here.
        self.chain_tracker.record_call(chain_id, req_event, event)

        # Breakpoint: hold the (already recorded) response until released.
        if self.paused:
            flow.intercept()
            self._intercepted.append(flow)
            log.info("Flow intercepted at breakpoint", tool=flow.metadata.get("tool"))

    async def _ensure_rules_loaded(self) -> None:
        if self.injector.loaded or self.redis is None:
            return
        try:
            raw = await self.redis.get(
                f"scan:{proxy_settings.scan_id}:injection_rules"
            )
            self.injector.load_from_raw(raw)
        except Exception as e:
            log.warning("Failed to load injection rules", error=str(e))

    # ------------------------------------------------------------------
    # Breakpoint control (Sprint 5)
    # ------------------------------------------------------------------

    async def _listen_control(self) -> None:
        """Apply pause/resume/step/inject commands from the control channel."""
        pubsub = self.redis.pubsub()
        channel = f"scan:{proxy_settings.scan_id}:control"
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    command = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                self._handle_control(command)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("Control listener stopped", error=str(e))
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:
                pass

    def _handle_control(self, command: dict) -> None:
        action = command.get("action")
        log.info("Breakpoint control", action=action)
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
            self._release(len(self._intercepted))
        elif action == "step":
            self._release(1)
        elif action == "inject":
            injection = command.get("injection") or {}
            self.injector.rules.append(
                {
                    "scenario": "manual-inject",
                    "tool": injection.get("tool", "*"),
                    "mode": injection.get("mode", "merge"),
                    "payload": injection.get("payload", {}),
                    "max_hits": int(injection.get("max_hits", 1)),
                }
            )

    def _release(self, count: int) -> None:
        """Resume up to ``count`` intercepted flows, oldest first."""
        for _ in range(min(count, len(self._intercepted))):
            flow = self._intercepted.pop(0)
            try:
                flow.resume()
            except Exception as e:
                log.warning("Failed to resume flow", error=str(e))

    def _route_to_mock(self, flow: http.HTTPFlow) -> None:
        host = flow.request.host
        for fragment, target in self._mock_hosts.items():
            if fragment in host:
                mock_host, _, mock_port = target.partition(":")
                flow.request.scheme = "http"
                flow.request.host = mock_host
                flow.request.port = int(mock_port or 80)
                return

    def _scan_uuid(self) -> uuid.UUID:
        if proxy_settings.scan_id != "unknown":
            return uuid.UUID(proxy_settings.scan_id)
        return uuid.uuid4()

    async def _next_seq(self) -> int:
        # Share the scan-wide sequence counter with the orchestrator so
        # (scan_id, seq) stays unique across both event producers.
        if self.redis is not None:
            try:
                return int(
                    await self.redis.incr(f"scan:{proxy_settings.scan_id}:seq")
                )
            except Exception as e:
                log.warning("Redis seq INCR failed; using local seq", error=str(e))
        self.seq += 1
        return self.seq

    async def _publish(self, event: EventEnvelope) -> None:
        if self.redis is None:
            return
        try:
            channel = f"scan:{event.scan_id}:events"
            await self.redis.publish(channel, event.model_dump_json())
        except Exception as e:
            log.warning("Failed to publish event", error=str(e))


addons = [SentinexAddon()]
