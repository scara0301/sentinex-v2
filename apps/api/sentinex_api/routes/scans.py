import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sentinex_core.db.repos import ScanRepo, AgentRepo
from ..deps import get_db, get_current_workspace

router = APIRouter()


class ScanCreate(BaseModel):
    agent_id: uuid.UUID
    scenario_ids: list[uuid.UUID] = []
    config: dict = {}


@router.post("", status_code=202)
async def start_scan(
    workspace_id: uuid.UUID,
    body: ScanCreate,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    # Validate agent belongs to workspace
    agent_repo = AgentRepo(db)
    agent = await agent_repo.get_by_id(body.agent_id)
    if not agent or agent.workspace_id != workspace_id:
        raise HTTPException(404, "Agent not found")

    # Create scan record
    scan_repo = ScanRepo(db)
    scan = await scan_repo.create(
        workspace_id=workspace_id,
        agent_id=body.agent_id,
        scenario_ids=body.scenario_ids,
        config=body.config,
    )
    await db.commit()

    # Enqueue ARQ job
    from arq import create_pool
    from arq.connections import RedisSettings
    from ..settings import settings

    redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    await redis.enqueue_job("run_scan", str(scan.id))
    await redis.close()

    return {"scan_id": scan.id, "status": scan.status}


@router.get("/{scan_id}")
async def get_scan(
    workspace_id: uuid.UUID,
    scan_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
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
async def list_scans(workspace_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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
