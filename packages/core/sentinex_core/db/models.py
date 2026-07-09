import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    String,
    Text,
    Numeric,
    BigInteger,
    Boolean,
    ARRAY,
    ForeignKey,
    Index,
    UniqueConstraint,
    LargeBinary,
    DateTime,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Workspace(Base):
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("api_key_hash", name="uq_workspaces_api_key_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    agents: Mapped[List["Agent"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    scans: Mapped[List["Scan"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    scenarios: Mapped[List["Scenario"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", "version", name="uq_agents_workspace_name_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    framework: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    manifest: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    bundle_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    workspace: Mapped["Workspace"] = relationship(back_populates="agents")
    tools: Mapped[List["Tool"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    scans: Mapped[List["Scan"]] = relationship(back_populates="agent")


class Tool(Base):
    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    side_effects: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String), nullable=True
    )

    agent: Mapped["Agent"] = relationship(back_populates="tools")


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", "version", name="uq_scenarios_workspace_slug_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(nullable=False)
    yaml: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workspace: Mapped[Optional["Workspace"]] = relationship(back_populates="scenarios")


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_ids: Mapped[Optional[List[uuid.UUID]]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=True
    )
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    workspace: Mapped["Workspace"] = relationship(back_populates="scans")
    agent: Mapped["Agent"] = relationship(back_populates="scans")
    events: Mapped[List["Event"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )
    findings: Mapped[List["Finding"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )
    badge: Mapped[Optional["Badge"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan", uselist=False
    )


class Event(Base):
    __tablename__ = "events"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    scan: Mapped["Scan"] = relationship(back_populates="events")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    cwe: Mapped[Optional[List[str]]] = mapped_column(ARRAY(String), nullable=True)
    remediation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("remediations.id", ondelete="SET NULL", deferrable=True, initially="DEFERRED"),
        nullable=True,
    )
    # "strong" (behavioral evidence, e.g. a call to the attacker host) or
    # "weak" (textual-only match, e.g. a marker string that a defensive
    # agent could echo without complying) — set by the firing detection rule.
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="strong")
    # "open" | "confirmed" | "dismissed" — set by a human via the review
    # endpoint. Gates badge/report issuance independently of confidence.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    scan: Mapped["Scan"] = relationship(back_populates="findings")
    # Not a true bidirectional pair with Remediation.finding below — each
    # side has its own physical FK column pointing at the other table
    # (remediation_id here, finding_id there), so there's no single shared
    # FK for SQLAlchemy to infer a consistent one-to-many/many-to-one
    # direction from. back_populates on both sides made SQLAlchemy treat
    # both as MANYTOONE and refuse to configure. Neither side is read via
    # the ORM relationship anywhere in the app (everything goes through
    # the *_id columns and repo methods), so declaring them independently
    # — each scoped to its own foreign_keys — is both correct and safe.
    remediation: Mapped[Optional["Remediation"]] = relationship(
        foreign_keys=[remediation_id]
    )


class Remediation(Base):
    __tablename__ = "remediations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    finding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    diff: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    playbook_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rescan_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scans.id", ondelete="SET NULL"),
        nullable=True,
    )

    # See the comment on Finding.remediation above — declared independently
    # rather than as a back_populates pair.
    finding: Mapped["Finding"] = relationship(foreign_keys=[finding_id])
    rescan: Mapped[Optional["Scan"]] = relationship(foreign_keys=[rescan_id])


class Badge(Base):
    __tablename__ = "badges"

    scan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True
    )
    svg: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    grade: Mapped[str] = mapped_column(String(4), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)

    scan: Mapped["Scan"] = relationship(back_populates="badge")


Index("ix_agents_workspace", Agent.workspace_id)
Index("ix_scans_workspace_created", Scan.workspace_id, Scan.created_at)
Index("ix_events_scan_ts", Event.scan_id, Event.ts)
Index("ix_findings_scan_severity", Finding.scan_id, Finding.severity)
Index("ix_findings_scan_status_severity", Finding.scan_id, Finding.status, Finding.severity)
