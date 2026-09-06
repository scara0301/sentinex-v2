"""
Request/response classification for the recording proxy.

Every outbound HTTP request the agent makes is turned into a tool-call
descriptor. Traffic to a *known* provider is named after that provider
(``stripe.post_refunds``); everything else is still recorded under the
generic ``http`` provider, keyed by hostname.

Recording unknown hosts is essential, not cosmetic: the built-in scenarios
detect exfiltration by matching the *host* an agent contacted
(``host_contains: [evil-archive]`` for return-path poisoning,
``host_not_contains: [openai, anthropic]`` for honeypot exfiltration). If
unrecognized hosts were dropped, an agent that fully complied with an
injected instruction would produce no events at all and the scan would
report a clean grade.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from mitmproxy import http

PROVIDER_PATTERNS = {
    "stripe.com": "stripe",
    "slack.com": "slack",
    "sendgrid.com": "sendgrid",
    "twilio.com": "twilio",
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "amazonaws.com": "aws",
}

# Provider used for any host that isn't a recognized SaaS API.
GENERIC_PROVIDER = "http"

# Longest request/response body fragment kept when the payload isn't JSON.
_MAX_RAW_CHARS = 2000

# A path segment is only useful in a tool name if it names an operation
# rather than an opaque identifier. Collapsing ids keeps call-volume
# detections meaningful: 200 calls to 200 different customers should share
# one tool name, not produce 200 distinct ones.
_ID_LIKE = re.compile(
    r"""
    ^\d+$                                   # 12345
    | ^[0-9a-fA-F]{8,}$                     # hex blob / sha
    | ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-       # uuid
      [0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$
    | ^[A-Za-z]{2,6}_[A-Za-z0-9]{8,}$       # provider id, e.g. cus_NffrFeUfNV2Hib
    | ^[A-Za-z0-9]{20,}$                    # long opaque token
    """,
    re.VERBOSE,
)


def _detect_provider(host: str) -> Optional[str]:
    for pattern, name in PROVIDER_PATTERNS.items():
        if pattern in host:
            return name
    return None


def _operation_segment(path: str) -> str:
    """Pick the last path segment that names an operation, skipping IDs.

    ``/v1/customers/cus_abc123`` -> ``customers`` rather than the customer id,
    so repeated calls to different objects collapse to one tool name.
    """
    segments = [s for s in path.split("?")[0].strip("/").split("/") if s]
    for segment in reversed(segments):
        if not _ID_LIKE.match(segment):
            return segment
    return segments[-1] if segments else "root"


def _coerce_mapping(value: Any, raw_key: str = "raw") -> dict:
    """Return a dict for any decoded JSON value.

    JSON bodies are legitimately arrays, strings, or numbers. The event
    payload models require a mapping, so a non-object is wrapped rather than
    passed through — previously this raised inside the mitmproxy hook and the
    whole tool call went unrecorded.
    """
    if isinstance(value, dict):
        return value
    return {raw_key: value}


def _decode_body(content: Optional[bytes], raw_key: str) -> dict:
    if not content:
        return {}
    try:
        return _coerce_mapping(json.loads(content), raw_key)
    except (ValueError, TypeError):
        return {raw_key: content.decode("utf-8", errors="replace")[:_MAX_RAW_CHARS]}


def classify_request(flow: http.HTTPFlow) -> dict:
    """Classify an HTTP request into a tool-call descriptor.

    Always returns a descriptor — every outbound request is recorded.
    """
    host = flow.request.pretty_host or flow.request.host or ""
    path = flow.request.path
    method = flow.request.method

    provider = _detect_provider(host)
    if provider:
        tool_name = f"{provider}.{method.lower()}_{_operation_segment(path)}"
    else:
        provider = GENERIC_PROVIDER
        # Host is part of the name so unknown-destination calls are
        # distinguishable in findings and in call-volume detections.
        tool_name = f"{GENERIC_PROVIDER}.{method.lower()}_{host}"

    args = _decode_body(flow.request.content, raw_key="body")
    if flow.request.query:
        # Query params are namespaced so they can't clobber body fields.
        args["query"] = dict(flow.request.query)

    return {
        "provider": provider,
        "tool": tool_name,
        "args": args,
        "host": host,
        "path": path,
        "method": method,
    }


def classify_connect_attempt(flow: http.HTTPFlow, error: str = "") -> dict:
    """Describe a CONNECT that never became a recordable request.

    For an ``https://`` URL the client sends ``CONNECT host:443`` first. If
    mitmproxy cannot reach upstream — an attacker host that does not resolve,
    or blocked egress — the tunnel is never established and the ``request``
    hook never fires. Without this, an agent that complied with an injected
    instruction and dialed the attacker host would leave no trace at all, and
    the scan would report a clean grade.

    Only the destination is knowable here: the request body was never sent,
    so ``args`` carries the failure reason rather than agent data.
    """
    host = flow.request.pretty_host or flow.request.host or ""
    provider = _detect_provider(host) or GENERIC_PROVIDER
    return {
        "provider": provider,
        "tool": f"{GENERIC_PROVIDER}.connect_{host}",
        "args": {"connect_failed": True, "error": error[:500]},
        "host": host,
        "path": "",
        "method": "CONNECT",
    }


def classify_response(flow: http.HTTPFlow) -> dict:
    """Extract the response payload for the recorded ``tool_result`` event."""
    if not flow.response:
        return {}
    return {
        "response": _decode_body(flow.response.content, raw_key="raw"),
        "status_code": flow.response.status_code,
    }
