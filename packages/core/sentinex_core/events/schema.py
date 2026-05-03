from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Per-type payload models  (match the plan's WS event schema exactly)
# ---------------------------------------------------------------------------

class ToolCallPayload(BaseModel):
    agent_id: str
    tool: str                          # e.g. "stripe.refund"
    args: dict[str, Any] = Field(default_factory=dict)
    transport: Literal["http", "mcp", "openai_tool"] = "http"
    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    host: Optional[str] = None


class ToolResultPayload(BaseModel):
    chain_id: str
    ok: bool
    duration_ms: int = 0
    response: dict[str, Any] = Field(default_factory=dict)
    injected: Union[bool, str] = False  # False or scenario slug name


class LLMMessagePayload(BaseModel):
    agent_id: str = "unknown"
    role: Literal["system", "user", "assistant", "tool"]
    content: Any
    tokens: dict[str, int] = Field(default_factory=dict)  # {"in": N, "out": N}
    model: Optional[str] = None


class FindingPayload(BaseModel):
    finding_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    category: str
    rule_id: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    title: str
    evidence_seqs: list[int] = Field(default_factory=list)


class StatePayload(BaseModel):
    from_: str = Field(alias="from")
    to: str
    reason: str = ""

    model_config = {"populate_by_name": True}


class ScenarioStepPayload(BaseModel):
    scenario: str
    step: int
    name: str
    result: Literal["ok", "fail", "skip"] = "ok"


class RiskUpdatePayload(BaseModel):
    score: float = Field(ge=0.0, le=100.0)
    delta: float
    drivers: list[str] = Field(default_factory=list)


class BreakpointPayload(BaseModel):
    action: Literal["pause", "resume", "step", "inject"]
    injection: Optional[dict[str, Any]] = None


class LogPayload(BaseModel):
    level: Literal["debug", "info", "warning", "error", "critical"]
    message: str
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Envelope  —  type is an explicit field, not derived from payload
# ---------------------------------------------------------------------------

EventType = Literal[
    "tool_call",
    "tool_result",
    "llm_message",
    "finding",
    "state",
    "scenario_step",
    "risk_update",
    "breakpoint",
    "log",
]

AnyPayload = Union[
    ToolCallPayload,
    ToolResultPayload,
    LLMMessagePayload,
    FindingPayload,
    StatePayload,
    ScenarioStepPayload,
    RiskUpdatePayload,
    BreakpointPayload,
    LogPayload,
]


class EventEnvelope(BaseModel):
    v: Literal[1] = 1
    scan_id: uuid.UUID
    seq: int
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: EventType
    payload: AnyPayload

    model_config = {"populate_by_name": True}
