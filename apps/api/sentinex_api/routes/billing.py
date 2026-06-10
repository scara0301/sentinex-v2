"""
Billing & usage endpoints (Sprint 5).

``POST /workspace/{id}/plan`` is the integration point for a payment
provider: a Stripe (or similar) webhook handler verifies the event and
calls this endpoint to flip the plan. Quota enforcement happens at the
resource-creation endpoints using the limits defined in
``sentinex_core.billing``.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sentinex_core.billing import (
    ACTIVE_SCAN_STATUSES,
    PLANS,
    current_period_start,
    get_plan,
)
from sentinex_core.db.repos import AgentRepo, ScanRepo, WorkspaceRepo

from ..deps import get_db, get_current_workspace

router = APIRouter()


@router.get("/usage")
async def get_usage(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    if workspace.id != workspace_id:
        raise HTTPException(404, "Workspace not found")

    plan = get_plan(workspace.plan)
    period_start = current_period_start()
    scan_repo = ScanRepo(db)
    scans_this_month = await scan_repo.count_created_since(workspace_id, period_start)
    active_scans = await scan_repo.count_active(workspace_id, ACTIVE_SCAN_STATUSES)
    agents = await AgentRepo(db).list_by_workspace(workspace_id)
    agent_names = {a.name for a in agents}

    return {
        "plan": plan.name,
        "period_start": period_start.isoformat(),
        "usage": {
            "scans_this_month": scans_this_month,
            "active_scans": active_scans,
            "agents": len(agent_names),
        },
        "limits": {
            "scans_per_month": plan.scans_per_month,
            "max_concurrent_scans": plan.max_concurrent_scans,
            "max_agents": plan.max_agents,
            "custom_scenarios": plan.custom_scenarios,
        },
    }


class PlanChange(BaseModel):
    plan: Literal["free", "pro", "enterprise"]


@router.post("/plan")
async def change_plan(
    workspace_id: uuid.UUID,
    body: PlanChange,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    if workspace.id != workspace_id:
        raise HTTPException(404, "Workspace not found")

    await WorkspaceRepo(db).update_plan(workspace_id, body.plan)
    await db.commit()
    plan = PLANS[body.plan]
    return {
        "plan": plan.name,
        "price_usd_month": plan.price_usd_month,
        "limits": {
            "scans_per_month": plan.scans_per_month,
            "max_concurrent_scans": plan.max_concurrent_scans,
            "max_agents": plan.max_agents,
            "custom_scenarios": plan.custom_scenarios,
        },
    }
