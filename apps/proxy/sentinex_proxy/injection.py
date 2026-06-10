"""
Response injection engine (Sprint 3).

The orchestrator serializes each scenario's injection rules into Redis at
``scan:{scan_id}:injection_rules`` before the proxy container starts. The
proxy loads them once and, for every intercepted tool response whose
classified tool name matches a rule, mutates the response body *before*
the agent sees it. The recorded ``tool_result`` event carries the
scenario slug in its ``injected`` field so detections can anchor on it.
"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from typing import Any, Optional, Union

import structlog
from mitmproxy import http

log = structlog.get_logger()


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class InjectionEngine:
    def __init__(self) -> None:
        self.rules: list[dict[str, Any]] = []
        self.loaded = False
        self._hits: dict[int, int] = {}

    def load(self, redis_client, scan_id: str) -> None:
        """Load rules from Redis (sync client; called from mitmproxy hooks)."""
        try:
            raw = redis_client.get(f"scan:{scan_id}:injection_rules")
        except Exception as exc:
            log.warning("Failed to load injection rules", error=str(exc))
            return
        self.loaded = True
        if not raw:
            return
        try:
            rules = json.loads(raw)
        except (TypeError, ValueError) as exc:
            log.warning("Invalid injection rules JSON", error=str(exc))
            return
        if isinstance(rules, list):
            self.rules = rules
            log.info("Injection rules loaded", count=len(self.rules))

    def apply(self, flow: http.HTTPFlow, tool_name: Optional[str]) -> Union[str, bool]:
        """
        Apply the first matching rule to ``flow.response``.

        Returns the owning scenario slug when an injection happened,
        else ``False``.
        """
        if not self.rules or not tool_name or flow.response is None:
            return False

        for idx, rule in enumerate(self.rules):
            if not fnmatch(tool_name, rule.get("tool", "*")):
                continue
            max_hits = int(rule.get("max_hits", 0) or 0)
            if max_hits and self._hits.get(idx, 0) >= max_hits:
                continue

            payload = rule.get("payload") or {}
            mode = rule.get("mode", "merge")
            if mode == "replace":
                body = payload
            else:
                try:
                    original = json.loads(flow.response.content or b"{}")
                except (TypeError, ValueError):
                    continue  # merge only makes sense for JSON bodies
                if not isinstance(original, dict):
                    continue
                body = _deep_merge(original, payload)

            flow.response.text = json.dumps(body)
            flow.response.headers["content-type"] = "application/json"
            self._hits[idx] = self._hits.get(idx, 0) + 1
            slug = str(rule.get("scenario", "unknown"))
            log.info("Injected response", tool=tool_name, scenario=slug)
            return slug
        return False
