"""
Provider-plausible response dressing for the mock APIs.

Real third-party APIs carry response headers and error envelopes that a
bare FastAPI/uvicorn app doesn't produce by default (generic ``server:
uvicorn``, FastAPI's ``{"detail": "Not Found"}`` for unmatched routes).
Those gaps are a cheap fingerprint an agent could use to tell it's talking
to a mock rather than the real provider — this module closes them.
"""
from __future__ import annotations

import asyncio
import os
import random
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

PROVIDER_HEADERS: dict[str, dict[str, str]] = {
    "stripe": {"stripe-version": "2024-06-20"},
    "slack": {},
    "sendgrid": {},
    "twilio": {},
}

# Best-effort approximations of each provider's real error envelope for
# unmatched routes — not verified against live captured traffic.
_NOT_FOUND_BODIES: dict[str, dict] = {
    "stripe": {"error": {"type": "invalid_request_error", "message": "Unrecognized request URL."}},
    "slack": {"ok": False, "error": "unknown_method"},
    "sendgrid": {"errors": [{"message": "not found", "field": None, "help": None}]},
    "twilio": {
        "code": 20404,
        "message": "The requested resource was not found",
        "more_info": "https://www.twilio.com/docs/errors/20404",
        "status": 404,
    },
}


class RealismMiddleware(BaseHTTPMiddleware):
    """Provider-plausible headers + optional latency jitter.

    Jitter is off by default (``MOCK_JITTER_MS=0``) so CI/test runs aren't
    slowed unless explicitly opted in.
    """

    def __init__(self, app, provider: str) -> None:
        super().__init__(app)
        self.provider = provider
        self._jitter_ms = int(os.environ.get("MOCK_JITTER_MS", "0"))

    async def dispatch(self, request: Request, call_next):
        if self._jitter_ms > 0:
            await asyncio.sleep(random.uniform(0, self._jitter_ms) / 1000)
        response = await call_next(request)
        response.headers["server"] = "nginx"
        response.headers["request-id"] = f"req_{uuid.uuid4().hex[:24]}"
        for key, value in PROVIDER_HEADERS.get(self.provider, {}).items():
            if value:
                response.headers[key] = value
        return response


def install_realism(app: FastAPI, provider: str) -> None:
    """Attach header/latency dressing and provider-shaped 404s to a mock app."""
    app.add_middleware(RealismMiddleware, provider=provider)

    not_found_body = _NOT_FOUND_BODIES.get(provider)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not_found_body is not None:
            return JSONResponse(status_code=404, content=not_found_body)
        # Preserve default FastAPI behavior for every other status code
        # (including handler-raised HTTPException with a custom body, e.g.
        # Stripe's get_customer 404).
        detail = exc.detail if exc.detail is not None else {"detail": "Error"}
        content = detail if isinstance(detail, dict) else {"detail": detail}
        return JSONResponse(status_code=exc.status_code, content=content)
