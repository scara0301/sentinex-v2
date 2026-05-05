import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sentinex_core.db.repos import ScanRepo, AgentRepo, EventRepo, FindingRepo
from ..deps import get_db, get_current_workspace

router = APIRouter()


class ScanCreate(BaseModel):
    agent_id: uuid.UUID
    scenario_ids: list[uuid.UUID] = []
    config: dict = {}


@router.post("", status_code=202)
async def start_scan(
    request: Request,
    workspace_id: uuid.UUID,
    body: ScanCreate,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    agent_repo = AgentRepo(db)
    agent = await agent_repo.get_by_id(body.agent_id)
    if not agent or agent.workspace_id != workspace_id:
        raise HTTPException(404, "Agent not found")

    scan_repo = ScanRepo(db)
    scan = await scan_repo.create(
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        scenario_ids=body.scenario_ids,
        config=body.config,
    )
    await db.commit()

    await request.app.state.arq_pool.enqueue_job("run_scan", str(scan.id))

    return {"scan_id": scan.id, "status": scan.status}


@router.get("/{scan_id}")
async def get_scan(
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    repo = ScanRepo(db)
    scan = await repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")
    return {
        "id": scan.id,
        "status": scan.status,
        "risk_score": scan.risk_score,
        "started_at": scan.started_at,
        "finished_at": scan.finished_at,
        "agent_id": scan.agent_id,
        "scenario_ids": scan.scenario_ids,
    }


@router.get("")
async def list_scans(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    repo = ScanRepo(db)
    scans = await repo.list_by_workspace(workspace_id, limit=50)
    return [
        {
            "id": s.id,
            "status": s.status,
            "risk_score": s.risk_score,
            "created_at": s.created_at,
        }
        for s in scans
    ]


@router.get("/{scan_id}/events")
async def list_scan_events(
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    from_seq: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    """Paginated event replay — used by the dashboard for step-through replay."""
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    event_repo = EventRepo(db)
    events = await event_repo.list_by_scan(scan_id, from_seq=from_seq, limit=limit)
    return {
        "events": [
            {
                "scan_id": str(ev.scan_id),
                "seq": ev.seq,
                "ts": ev.ts.isoformat() if ev.ts else None,
                "type": ev.type,
                "payload": ev.payload,
            }
            for ev in events
        ],
        "count": len(events),
        "has_more": len(events) == limit,
    }


@router.get("/{scan_id}/findings")
async def list_scan_findings(
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    severity: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    """List findings for a completed scan, optionally filtered by severity."""
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    finding_repo = FindingRepo(db)
    findings = await finding_repo.list_by_scan(scan_id, severity=severity)
    return [
        {
            "id": f.id,
            "category": f.category,
            "rule_id": f.rule_id,
            "severity": f.severity,
            "title": f.title,
            "evidence": f.evidence,
            "cwe": f.cwe,
            "created_at": f.created_at,
        }
        for f in findings
    ]

