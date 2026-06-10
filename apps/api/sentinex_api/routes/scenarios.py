import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, field_validator

from sentinex_core.db.repos import ScenarioRepo
from ..deps import get_db, get_current_workspace

router = APIRouter()


class ScenarioCreate(BaseModel):
    name: str
    description: Optional[str] = None
    yaml_dsl: str
    tags: list[str] = []

    @field_validator("yaml_dsl")
    @classmethod
    def _non_empty_yaml(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("yaml_dsl must not be empty")
        return v


@router.post("", status_code=201)
async def create_scenario(
    body: ScenarioCreate,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    from sentinex_core.scenarios import parse_scenario_yaml

    try:
        spec = parse_scenario_yaml(body.yaml_dsl)
    except ValueError as exc:
        raise HTTPException(422, f"Invalid scenario DSL: {exc}") from exc

    repo = ScenarioRepo(db)
    scenario = await repo.create(
        workspace_id=workspace.id,
        name=body.name,
        slug=spec.slug,
        description=body.description or spec.description,
        yaml_dsl=body.yaml_dsl,
        parsed=spec.model_dump(mode="json"),
        tags=body.tags or spec.tags,
        builtin=False,
    )
    await db.commit()

    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "tags": scenario.tags,
        "builtin": scenario.builtin,
    }


@router.get("")
async def list_scenarios(
    db: AsyncSession = Depends(get_db),
):
    """Return all scenarios — both builtin (platform-wide) and workspace custom ones."""
    repo = ScenarioRepo(db)
    scenarios = await repo.list_all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "tags": s.tags,
            "builtin": s.builtin,
        }
        for s in scenarios
    ]


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    repo = ScenarioRepo(db)
    scenario = await repo.get_by_id(scenario_id)
    if not scenario:
        raise HTTPException(404, "Scenario not found")
    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "yaml": scenario.yaml,
        "tags": scenario.tags,
        "builtin": scenario.builtin,
    }
