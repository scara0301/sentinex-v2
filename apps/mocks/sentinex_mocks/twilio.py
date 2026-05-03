"""
Mock Twilio API facade.

Covers:
  POST /2010-04-01/Accounts/{account_sid}/Messages.json  — send SMS
  GET  /2010-04-01/Accounts/{account_sid}/Messages.json  — list messages
"""
from __future__ import annotations

import time
import uuid
import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="SENTINEX Mock Twilio", docs_url=None)
log = structlog.get_logger()


def _log(method: str, path: str, body: dict) -> None:
    log.info("mock_twilio_request", method=method, path=path, body=body)


_SENT_MESSAGES: list[dict] = []


class SendMessageBody(BaseModel):
    To: str
    From: str
    Body: str
    StatusCallback: Optional[str] = None


@app.post("/2010-04-01/Accounts/{account_sid}/Messages.json")
async def send_message(account_sid: str, body: SendMessageBody):
    _log("POST", f"/2010-04-01/Accounts/{account_sid}/Messages.json", body.model_dump())
    msg = {
        "sid": f"SM{uuid.uuid4().hex[:32]}",
        "account_sid": account_sid,
        "to": body.To,
        "from": body.From,
        "body": body.Body,
        "status": "queued",
        "direction": "outbound-api",
        "date_created": time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()),
        "date_sent": None,
        "price": "-0.00750",
        "price_unit": "USD",
        "uri": f"/2010-04-01/Accounts/{account_sid}/Messages/SM{uuid.uuid4().hex[:32]}.json",
    }
    _SENT_MESSAGES.append(msg)
    return msg


@app.get("/2010-04-01/Accounts/{account_sid}/Messages.json")
async def list_messages(account_sid: str):
    _log("GET", f"/2010-04-01/Accounts/{account_sid}/Messages.json", {})
    return {
        "messages": _SENT_MESSAGES,
        "page": 0,
        "page_size": 50,
        "first_page_uri": "",
        "next_page_uri": None,
        "uri": f"/2010-04-01/Accounts/{account_sid}/Messages.json",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "provider": "twilio"}
