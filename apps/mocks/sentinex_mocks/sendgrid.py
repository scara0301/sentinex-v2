"""
Mock SendGrid API facade.

Covers:
  POST /v3/mail/send
  GET  /v3/suppression/bounces
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Any

app = FastAPI(title="SENTINEX Mock SendGrid", docs_url=None)
log = structlog.get_logger()


def _log(method: str, path: str, body: dict) -> None:
    log.info("mock_sendgrid_request", method=method, path=path, body=body)


_SENT_EMAILS: list[dict] = []


class EmailAddress(BaseModel):
    email: str
    name: Optional[str] = None


class Personalization(BaseModel):
    to: list[EmailAddress]
    subject: Optional[str] = None


class EmailContent(BaseModel):
    type: str
    value: str


class SendEmailBody(BaseModel):
    personalizations: list[Personalization]
    from_: Optional[EmailAddress] = None
    subject: Optional[str] = None
    content: list[EmailContent] = []
    reply_to: Optional[EmailAddress] = None

    model_config = {"populate_by_name": True}


@app.post("/v3/mail/send", status_code=202)
async def send_email(body: SendEmailBody):
    record = body.model_dump()
    _log("POST", "/v3/mail/send", record)
    _SENT_EMAILS.append(record)
    return {}  # 202 no body


@app.get("/v3/suppression/bounces")
async def list_bounces():
    return []


@app.get("/health")
async def health():
    return {"status": "ok", "provider": "sendgrid"}
