import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sentinex_core.db.models import Event as EventModel
from sentinex_core.db.repos import (
    ScanRepo,
    AgentRepo,
    EventRepo,
    FindingRepo,
    RemediationRepo,
    ScenarioRepo,
)
from sentinex_core.events.schema import BreakpointPayload, EventEnvelope
from ..deps import get_db, get_authorized_workspace
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
    workspace=Depends(get_authorized_workspace),
):
    agent_repo = AgentRepo(db)
    agent = await agent_repo.get_by_id(body.agent_id)
    if not agent or agent.workspace_id != workspace_id:
        raise HTTPException(404, "Agent not found")

    # Every requested scenario must exist and be visible to this workspace.
    # Without this a caller could name another tenant's private scenario
    # UUIDs (the worker loads them by id with no ownership filter), and a
    # mistyped id would silently produce a scan that runs nothing.
    if body.scenario_ids:
        scenario_repo = ScenarioRepo(db)
        found = await scenario_repo.get_by_ids(list(body.scenario_ids))
        visible = {
            sc.id
            for sc in found
            if sc.builtin or sc.workspace_id == workspace_id
        }
        missing = [str(sid) for sid in body.scenario_ids if sid not in visible]
        if missing:
            raise HTTPException(
                404, f"Unknown or inaccessible scenario(s): {', '.join(missing)}"
            )

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
    workspace=Depends(get_authorized_workspace),
):
    repo = ScanRepo(db)
    scan = await repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    pending_review = None
    if scan.status == "DONE":
        blocking = await FindingRepo(db).count_unreviewed_blocking(scan_id)
        pending_review = blocking > 0

    return {
        "id": scan.id,
        "status": scan.status,
        "risk_score": scan.risk_score,
        "started_at": scan.started_at,
        "finished_at": scan.finished_at,
        "agent_id": scan.agent_id,
        "scenario_ids": scan.scenario_ids,
        "pending_review": pending_review,
    }


@router.get("")
async def list_scans(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
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
    workspace=Depends(get_authorized_workspace),
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
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    """List findings for a completed scan, optionally filtered by severity/status."""
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    finding_repo = FindingRepo(db)
    findings = await finding_repo.list_by_scan(scan_id, severity=severity, status=status)
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
                "confidence": f.confidence,
                "status": f.status,
                "reviewed_at": f.reviewed_at,
                "reviewed_by": f.reviewed_by,
                "review_note": f.review_note,
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


class FindingReviewRequest(BaseModel):
    status: Literal["open", "confirmed", "dismissed"]
    reviewer: Optional[str] = None
    note: Optional[str] = None


@router.post("/{scan_id}/findings/{finding_id}/review")
async def review_finding(
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: FindingReviewRequest,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    """Record a human review decision (confirm/dismiss/reopen) for a finding.

    Gates badge/report issuance (via ``count_unreviewed_blocking``) and
    whether ``/fix`` can be applied — a dismissed finding is presumed a
    false positive and its remediation should not be auto-applied.
    """
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")

    finding_repo = FindingRepo(db)
    finding = await finding_repo.get_by_id(finding_id)
    if not finding or finding.scan_id != scan_id:
        raise HTTPException(404, "Finding not found for this scan")

    await finding_repo.update_review(
        finding_id,
        status=body.status,
        reviewer=body.reviewer or workspace.name,
        note=body.note,
    )
    await db.commit()

    finding = await finding_repo.get_by_id(finding_id)
    if finding is None:  # deleted between the update and the re-read
        raise HTTPException(404, "Finding not found for this scan")
    return {
        "id": finding.id,
        "status": finding.status,
        "confidence": finding.confidence,
        "reviewed_at": finding.reviewed_at,
        "reviewed_by": finding.reviewed_by,
        "review_note": finding.review_note,
    }


@router.get("/{scan_id}/report")
async def get_scan_report(
    request: Request,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
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

    blocking = await FindingRepo(db).count_unreviewed_blocking(scan_id)
    if blocking > 0:
        raise HTTPException(
            409,
            {
                "status": "pending_review",
                "unreviewed_findings": blocking,
                "detail": "Report withheld pending review of critical/high findings.",
            },
        )

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

    # Dedupe: a fixed job id means repeated polls don't pile up renders.
    await request.app.state.arq_pool.enqueue_job(
        "render_report", str(scan_id), _job_id=f"render_report:{scan_id}"
    )
    return JSONResponse(
        {"status": "rendering", "detail": "Report queued; retry shortly."},
        status_code=202,
    )


# Breakpoints only make sense once the proxy exists and the agent is running.
_CONTROLLABLE_STATUSES = {"RUNNING", "PAUSED"}


class ControlRequest(BaseModel):
    action: Literal["pause", "resume", "step", "inject"]
    # For action=inject: {"tool": "stripe.*", "mode": "merge", "payload": {...}}
    injection: Optional[dict] = None


@router.post("/{scan_id}/control", status_code=202)
async def control_scan(
    request: Request,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    body: ControlRequest,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
):
    """
    Breakpoint control for a live scan (Sprint 5).

    The proxy holds intercepted tool responses while paused; ``step``
    releases exactly one, ``resume`` releases all, and ``inject`` adds an
    ad-hoc one-shot response-poisoning rule.
    """
    scan_repo = ScanRepo(db)
    scan = await scan_repo.get_by_id(scan_id)
    if not scan or scan.workspace_id != workspace_id:
        raise HTTPException(404, "Scan not found")
    if scan.status not in _CONTROLLABLE_STATUSES:
        raise HTTPException(
            409, f"Scan is not controllable in status {scan.status}"
        )
    if body.action == "inject" and not body.injection:
        raise HTTPException(422, "action=inject requires an injection object")

    redis = request.app.state.arq_pool

    # 1. Tell the proxy.
    await redis.publish(
        f"scan:{scan_id}:control",
        json.dumps({"action": body.action, "injection": body.injection}),
    )

    # 2. Record + broadcast a breakpoint event so dashboards see it.
    seq = int(await redis.incr(f"scan:{scan_id}:seq"))
    ts = datetime.now(timezone.utc)
    envelope = EventEnvelope(
        scan_id=scan_id,
        seq=seq,
        ts=ts,
        type="breakpoint",
        payload=BreakpointPayload(action=body.action, injection=body.injection),
    )
    await redis.publish(f"scan:{scan_id}:events", envelope.model_dump_json())
    await EventRepo(db).bulk_insert(
        [
            EventModel(
                scan_id=scan_id,
                seq=seq,
                ts=ts,
                type="breakpoint",
                payload=envelope.payload.model_dump(),
            )
        ]
    )

    # 3. Reflect pause state on the scan record.
    if body.action == "pause":
        await scan_repo.update_status(scan_id, "PAUSED")
    elif body.action in ("resume", "step") and scan.status == "PAUSED":
        await scan_repo.update_status(scan_id, "RUNNING")
    await db.commit()

    return {"status": "sent", "action": body.action, "seq": seq}


class FixRequest(BaseModel):
    finding_id: uuid.UUID


@router.post("/{scan_id}/fix", status_code=202)
async def apply_scan_fix(
    request: Request,
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    body: FixRequest,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_authorized_workspace),
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
    if finding.status == "dismissed":
        raise HTTPException(409, "Finding was dismissed in review; cannot apply fix")
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
        "apply_fix",
        str(scan_id),
        str(remediation.id),
        _job_id=f"apply_fix:{remediation.id}",
    )
    return {"status": "queued", "remediation_id": remediation.id}

