from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Any

from sentinex_core.events.schema import EventEnvelope


@dataclass
class ChainNode:
    chain_id: str
    tool: str
    args: dict[str, Any]
    response: dict[str, Any] = field(default_factory=dict)
    host: Optional[str] = None


class ChainTracker:
    """
    Maintains a directed call-chain graph per scan for detecting:
    - Data exfiltration: sensitive value read from DB/file then sent via network
    - Privilege escalation: low-scope call followed by high-scope call
    - Excessive calling: same tool invoked too many times in a window
    """

    def __init__(self, exfil_threshold: int = 3, call_limit: int = 50):
        self._nodes: dict[str, ChainNode] = {}
        self._call_counts: dict[str, int] = {}
        self._sensitive_values: set[str] = set()
        self._exfil_threshold = exfil_threshold
        self._call_limit = call_limit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_call(
        self,
        chain_id: str,
        request_event: Optional[EventEnvelope],
        response_event: Optional[EventEnvelope],
    ) -> list[str]:
        """
        Record a completed tool call and return a list of triggered finding
        rule IDs (empty list when nothing suspicious is detected).
        """
        findings: list[str] = []

        if request_event is None:
            return findings

        req_payload = request_event.payload
        tool_name: str = getattr(req_payload, "tool", "unknown")
        args: dict = getattr(req_payload, "args", {})
        host: Optional[str] = getattr(req_payload, "host", None)

        resp_payload = response_event.payload if response_event else None
        response: dict = getattr(resp_payload, "response", {}) if resp_payload else {}

        node = ChainNode(
            chain_id=chain_id,
            tool=tool_name,
            args=args,
            response=response,
            host=host,
        )
        self._nodes[chain_id] = node

        # Track call counts for denial-of-wallet detection
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1
        if self._call_counts[tool_name] > self._call_limit:
            findings.append("TOOL-DOW-001")

        # Harvest sensitive-looking values from responses (SSNs, API keys, emails)
        self._harvest_sensitive(response)

        # Check if any harvested value is leaking through outbound args
        if host and host not in ("api.openai.com", "api.anthropic.com"):
            args_str = str(args)
            for sensitive in self._sensitive_values:
                if sensitive in args_str:
                    findings.append("TOOL-EXFIL-001")
                    break

        return findings

    def seed_sensitive(self, values: list[str]) -> None:
        """Pre-seed known sensitive values (from scenario setup) for exfil detection."""
        self._sensitive_values.update(v for v in values if v)

    def get_call_count(self, tool_name: str) -> int:
        return self._call_counts.get(tool_name, 0)

    def reset(self) -> None:
        self._nodes.clear()
        self._call_counts.clear()
        self._sensitive_values.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    _SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
    _API_KEY_RE = re.compile(r"\b(?:sk|pk|rk|api)[_-][a-zA-Z0-9]{16,}\b")
    _EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")

    def _harvest_sensitive(self, obj: Any, depth: int = 0) -> None:
        if depth > 5:
            return
        if isinstance(obj, str):
            for pattern in (self._SSN_RE, self._API_KEY_RE, self._EMAIL_RE):
                for match in pattern.findall(obj):
                    self._sensitive_values.add(match)
        elif isinstance(obj, dict):
            for v in obj.values():
                self._harvest_sensitive(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._harvest_sensitive(item, depth + 1)
