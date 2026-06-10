import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sentinex_core.db.repos import (
    ScanRepo,
    AgentRepo,
    EventRepo,
    FindingRepo,
    RemediationRepo,
)
from ..deps import get_db, get_current_workspace
from ..settings import settings

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
    remediations = await RemediationRepo(db).list_by_finding_ids(
        [f.id for f in findings]
    )
    remediation_by_finding = {r.finding_id: r for r in remediations}
    out = []
    for f in findings:
        r = remediation_by_finding.get(f.id)
        out.append(
            {
                "id": f.id,
                "category": f.category,
                "rule_id": f.rule_id,
                "severity": f.severity,
                "title": f.title,
                "evidence": f.evidence,
                "cwe": f.cwe,
                "created_at": f.created_at,
                "remediation": {
                    "id": r.id,
                    "playbook_md": r.playbook_md,
                    "has_patch": bool(r.diff),
                    "applied": r.applied,
                    "rescan_id": r.rescan_id,
                }
                if r
                else None,
            }
        )
    return out


@router.get("/{scan_id}/report")
async def get_scan_report(
    request: Request,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    """
    Download the compliance report for a finished scan.

    Returns the PDF (or HTML fallback) if it has been rendered; otherwise
    enqueues rendering and responds 202 so clients can poll.
    """
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    report_dir = Path(settings.report_dir)
    pdf_path = report_dir / f"{scan_id}.pdf"
    if pdf_path.exists():
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"sentinex-report-{scan_id}.pdf",
        )
    html_path = report_dir / f"{scan_id}.html"
    if html_path.exists():
        return FileResponse(html_path, media_type="text/html")

    if scan.status != "DONE":
        raise HTTPException(409, f"Scan is not finished (status={scan.status})")

    await request.app.state.arq_pool.enqueue_job("render_report", str(scan_id))
    return JSONResponse(
        {"status": "rendering", "detail": "Report queued; retry shortly."},
        status_code=202,
    )


class FixRequest(BaseModel):
    finding_id: uuid.UUID


@router.post("/{scan_id}/fix", status_code=202)
async def apply_scan_fix(
    request: Request,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    body: FixRequest,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    """
    Apply a finding's auto-generated patch to a copy of the agent bundle
    and enqueue a verification rescan.
    """
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    finding = await FindingRepo(db).get_by_id(body.finding_id)
    if not finding or finding.scan_id != scan_id:
        raise HTTPException(404, "Finding not found for this scan")
    if not finding.remediation_id:
        raise HTTPException(409, "Finding has no remediation")

    remediation = await RemediationRepo(db).get_by_id(finding.remediation_id)
    if not remediation or not remediation.diff:
        raise HTTPException(
            409, "Remediation is playbook-only; no machine-applicable patch"
        )
    if remediation.applied:
        raise HTTPException(409, "Remediation already applied")

    await request.app.state.arq_pool.enqueue_job(
        "apply_fix", str(scan_id), str(remediation.id)
    )
    return {"status": "queued", "remediation_id": remediation.id}

