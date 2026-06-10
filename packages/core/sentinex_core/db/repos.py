import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Agent, Badge, Event, Finding, Remediation, Scan, Scenario, Workspace


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
            created_at=datetime.now(timezone.utc),
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

    async def update_plan(self, workspace_id: uuid.UUID, plan: str) -> None:
        await self._session.execute(
            update(Workspace).where(Workspace.id == workspace_id).values(plan=plan)
        )


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
            created_at=datetime.now(timezone.utc),
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
            created_at=datetime.now(timezone.utc),
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

    async def count_created_since(
        self, workspace_id: uuid.UUID, since: datetime
    ) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Scan)
            .where(Scan.workspace_id == workspace_id, Scan.created_at >= since)
        )
        return int(result.scalar_one())

    async def count_active(
        self, workspace_id: uuid.UUID, active_statuses: tuple[str, ...]
    ) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Scan)
            .where(
                Scan.workspace_id == workspace_id,
                Scan.status.in_(active_statuses),
            )
        )
        return int(result.scalar_one())


class EventRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def bulk_insert(self, events: list[Event]) -> None:
        self._session.add_all(events)
        await self._session.flush()

    async def bulk_upsert(self, rows: list[dict]) -> None:
        """Insert event rows, ignoring (scan_id, seq) collisions.

        Used for proxy-originated events where the sequence counter is
        best-effort (a Redis hiccup can produce duplicate seqs).
        """
        if not rows:
            return
        stmt = pg_insert(Event).values(rows).on_conflict_do_nothing(
            index_elements=["scan_id", "seq"]
        )
        await self._session.execute(stmt)

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
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(finding)
        await self._session.flush()
        return finding

    async def get_by_id(self, finding_id: uuid.UUID) -> Optional[Finding]:
        result = await self._session.execute(
            select(Finding).where(Finding.id == finding_id)
        )
        return result.scalar_one_or_none()

    async def set_remediation(
        self, finding_id: uuid.UUID, remediation_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            update(Finding)
            .where(Finding.id == finding_id)
            .values(remediation_id=remediation_id)
        )

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


class ScenarioRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        workspace_id: Optional[uuid.UUID],
        name: str,
        description: Optional[str] = None,
        yaml_dsl: Optional[str] = None,
        parsed: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        builtin: bool = False,
        slug: Optional[str] = None,
        id: Optional[uuid.UUID] = None,
    ) -> Scenario:
        scenario = Scenario(
            id=id or uuid.uuid4(),
            workspace_id=workspace_id,
            slug=slug or name.lower().replace(" ", "-"),
            version=1,
            name=name,
            description=description,
            yaml=yaml_dsl,
            parsed=parsed,
            tags=tags or [],
            builtin=builtin,
        )
        self._session.add(scenario)
        await self._session.flush()
        return scenario

    async def list_all(self) -> Sequence[Scenario]:
        result = await self._session.execute(
            select(Scenario).order_by(Scenario.builtin.desc(), Scenario.name)
        )
        return result.scalars().all()

    async def get_by_id(self, scenario_id: uuid.UUID) -> Optional[Scenario]:
        result = await self._session.execute(
            select(Scenario).where(Scenario.id == scenario_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ids(self, scenario_ids: list[uuid.UUID]) -> Sequence[Scenario]:
        if not scenario_ids:
            return []
        result = await self._session.execute(
            select(Scenario).where(Scenario.id.in_(scenario_ids))
        )
        return result.scalars().all()

    async def get_builtin_by_slug(self, slug: str) -> Optional[Scenario]:
        result = await self._session.execute(
            select(Scenario).where(
                Scenario.slug == slug,
                Scenario.builtin.is_(True),
                Scenario.workspace_id.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def update_definition(
        self,
        scenario_id: uuid.UUID,
        *,
        name: str,
        description: Optional[str],
        yaml_dsl: str,
        parsed: dict,
        tags: list[str],
    ) -> None:
        await self._session.execute(
            update(Scenario)
            .where(Scenario.id == scenario_id)
            .values(
                name=name,
                description=description,
                yaml=yaml_dsl,
                parsed=parsed,
                tags=tags,
            )
        )


class RemediationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        finding_id: uuid.UUID,
        diff: Optional[str] = None,
        playbook_md: Optional[str] = None,
        id: Optional[uuid.UUID] = None,
    ) -> Remediation:
        remediation = Remediation(
            id=id or uuid.uuid4(),
            finding_id=finding_id,
            diff=diff,
            playbook_md=playbook_md,
            applied=False,
        )
        self._session.add(remediation)
        await self._session.flush()
        return remediation

    async def get_by_id(self, remediation_id: uuid.UUID) -> Optional[Remediation]:
        result = await self._session.execute(
            select(Remediation).where(Remediation.id == remediation_id)
        )
        return result.scalar_one_or_none()

    async def list_by_finding_ids(
        self, finding_ids: list[uuid.UUID]
    ) -> Sequence[Remediation]:
        if not finding_ids:
            return []
        result = await self._session.execute(
            select(Remediation).where(Remediation.finding_id.in_(finding_ids))
        )
        return result.scalars().all()

    async def mark_applied(
        self, remediation_id: uuid.UUID, rescan_id: Optional[uuid.UUID]
    ) -> None:
        await self._session.execute(
            update(Remediation)
            .where(Remediation.id == remediation_id)
            .values(applied=True, rescan_id=rescan_id)
        )


class BadgeRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, scan_id: uuid.UUID) -> Optional[Badge]:
        result = await self._session.execute(
            select(Badge).where(Badge.scan_id == scan_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        scan_id: uuid.UUID,
        svg: bytes,
        grade: str,
        signed_at: datetime,
        signature: str,
    ) -> Badge:
        badge = Badge(
            scan_id=scan_id,
            svg=svg,
            grade=grade,
            signed_at=signed_at,
            signature=signature,
        )
        self._session.add(badge)
        await self._session.flush()
        return badge
