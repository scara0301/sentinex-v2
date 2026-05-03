import uuid
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent, Event, Finding, Scan, Workspace


class WorkspaceRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        name: str,
        api_key_hash: str,
        id: Optional[uuid.UUID] = None,
    ) -> Workspace:
        workspace = Workspace(
            id=id or uuid.uuid4(),
            name=name,
            api_key_hash=api_key_hash,
            created_at=datetime.utcnow(),
        )
        self._session.add(workspace)
        await self._session.flush()
        return workspace

    async def get_by_id(self, workspace_id: uuid.UUID) -> Optional[Workspace]:
        result = await self._session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        return result.scalar_one_or_none()

    async def get_by_api_key(self, api_key_hash: str) -> Optional[Workspace]:
        result = await self._session.execute(
            select(Workspace).where(Workspace.api_key_hash == api_key_hash)
        )
        return result.scalar_one_or_none()


class AgentRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        name: str,
        framework: str,
        version: int,
        manifest: Optional[dict] = None,
        bundle_uri: Optional[str] = None,
        id: Optional[uuid.UUID] = None,
    ) -> Agent:
        agent = Agent(
            id=id or uuid.uuid4(),
            workspace_id=workspace_id,
            name=name,
            framework=framework,
            version=version,
            manifest=manifest,
            bundle_uri=bundle_uri,
            created_at=datetime.utcnow(),
        )
        self._session.add(agent)
        await self._session.flush()
        return agent

    async def get_by_id(self, agent_id: uuid.UUID) -> Optional[Agent]:
        result = await self._session.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def list_by_workspace(self, workspace_id: uuid.UUID) -> Sequence[Agent]:
        result = await self._session.execute(
            select(Agent)
            .where(Agent.workspace_id == workspace_id)
            .order_by(Agent.name, Agent.version)
        )
        return result.scalars().all()


class ScanRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        status: str = "PENDING",
        scenario_ids: Optional[list[uuid.UUID]] = None,
        config: Optional[dict] = None,
        id: Optional[uuid.UUID] = None,
    ) -> Scan:
        scan = Scan(
            id=id or uuid.uuid4(),
            workspace_id=workspace_id,
            agent_id=agent_id,
            status=status,
            scenario_ids=scenario_ids,
            config=config,
            created_at=datetime.utcnow(),
        )
        self._session.add(scan)
        await self._session.flush()
        return scan

    async def get_by_id(self, scan_id: uuid.UUID) -> Optional[Scan]:
        result = await self._session.execute(
            select(Scan).where(Scan.id == scan_id)
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        scan_id: uuid.UUID,
        status: str,
        *,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> None:
        values: dict = {"status": status}
        if started_at is not None:
            values["started_at"] = started_at
        if finished_at is not None:
            values["finished_at"] = finished_at
        await self._session.execute(
            update(Scan).where(Scan.id == scan_id).values(**values)
        )

    async def update_risk_score(
        self, scan_id: uuid.UUID, risk_score: float
    ) -> None:
        await self._session.execute(
            update(Scan).where(Scan.id == scan_id).values(risk_score=risk_score)
        )

    async def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Scan]:
        result = await self._session.execute(
            select(Scan)
            .where(Scan.workspace_id == workspace_id)
            .order_by(Scan.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()


class EventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, events: list[Event]) -> None:
        self._session.add_all(events)
        await self._session.flush()

    async def list_by_scan(
        self,
        scan_id: uuid.UUID,
        from_seq: int = 0,
        limit: int = 1000,
    ) -> Sequence[Event]:
        result = await self._session.execute(
            select(Event)
            .where(Event.scan_id == scan_id, Event.seq >= from_seq)
            .order_by(Event.seq)
            .limit(limit)
        )
        return result.scalars().all()


class FindingRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        scan_id: uuid.UUID,
        category: str,
        rule_id: str,
        severity: str,
        title: str,
        evidence: Optional[dict] = None,
        cwe: Optional[list[str]] = None,
        remediation_id: Optional[uuid.UUID] = None,
        id: Optional[uuid.UUID] = None,
    ) -> Finding:
        finding = Finding(
            id=id or uuid.uuid4(),
            scan_id=scan_id,
            category=category,
            rule_id=rule_id,
            severity=severity,
            title=title,
            evidence=evidence,
            cwe=cwe,
            remediation_id=remediation_id,
            created_at=datetime.utcnow(),
        )
        self._session.add(finding)
        await self._session.flush()
        return finding

    async def list_by_scan(
        self,
        scan_id: uuid.UUID,
        *,
        severity: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Sequence[Finding]:
        stmt = select(Finding).where(Finding.scan_id == scan_id)
        if severity is not None:
            stmt = stmt.where(Finding.severity == severity)
        stmt = stmt.order_by(Finding.created_at).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return result.scalars().all()
