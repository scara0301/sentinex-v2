from mitmproxy import http
import json
from typing import Optional

PROVIDER_PATTERNS = {
    "stripe.com": "stripe",
    "slack.com": "slack",
    "sendgrid.com": "sendgrid",
    "twilio.com": "twilio",
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "amazonaws.com": "aws",
}

def _detect_provider(host: str) -> Optional[str]:
    for pattern, name in PROVIDER_PATTERNS.items():
        if pattern in host:
            return name
    return None

def classify_request(flow: http.HTTPFlow) -> Optional[dict]:
    """
    Classify an HTTP request into a tool call descriptor.
    Returns dict with {provider, tool, args} or None if uninteresting.
    """
    host = flow.request.host
    provider = _detect_provider(host)
    if not provider:
        return None

    path = flow.request.path
    method = flow.request.method

    # Try to parse JSON body as args
    args = {}
    if flow.request.content:
        try:
            args = json.loads(flow.request.content)
        except Exception:
            args = {"raw": flow.request.content.decode("utf-8", errors="replace")[:500]}

    # Add query params
    if flow.request.query:
        args.update(dict(flow.request.query))

    tool_name = f"{provider}.{method.lower()}{path.rstrip('/').split('/')[-1]}"

    return {
        "provider": provider,
        "tool": tool_name,
        "args": args,
        "host": host,
        "path": path,
        "method": method,
    }

def classify_response(flow: http.HTTPFlow) -> dict:
    """Extract response payload for logging."""
    if not flow.response:
        return {}
    response = {}
    if flow.response.content:
        try:
            response = json.loads(flow.response.content)
        except Exception:
            response = {"raw": flow.response.content.decode("utf-8", errors="replace")[:1000]}
    return {"response": response, "status_code": flow.response.status_code}
