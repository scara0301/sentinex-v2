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


def _validate_yaml_dsl(yaml_str: str) -> tuple[bool, Optional[str]]:
    """
    Validate scenario YAML via sentinex_core DSL validator.
    Returns (is_valid, error_message).
    Falls back to basic YAML parse check if sentinex_core is unavailable.
    """
    try:
        from sentinex_core.scenarios.dsl import validate_scenario_yaml

        validate_scenario_yaml(yaml_str)
        # raises on invalid; if it returns, we're good
        return True, None
    except ImportError:
        # sentinex_core DSL validator not yet available — fall back to yaml parse
        try:
            import yaml

            yaml.safe_load(yaml_str)
            return True, None
        except Exception as exc:
            return False, str(exc)
    except Exception as exc:
        return False, str(exc)


@router.post("", status_code=201)
async def create_scenario(
    body: ScenarioCreate,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    is_valid, error = _validate_yaml_dsl(body.yaml_dsl)
    if not is_valid:
        raise HTTPException(422, f"Invalid scenario DSL: {error}")

    repo = ScenarioRepo(db)
    scenario = await repo.create(
        workspace_id=workspace.id,
        name=body.name,
        description=body.description,
        yaml_dsl=body.yaml_dsl,
        tags=body.tags,
        builtin=False,
    )
    await db.commit()

    return {
        "id": scenario.id,
        "name": scenario.name,
        "description": scenario.description,
        "tags": scenario.tags,
        "builtin": scenario.builtin,
        "created_at": scenario.created_at,
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
            "created_at": s.created_at,
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
        "yaml_dsl": scenario.yaml_dsl,
        "tags": scenario.tags,
        "builtin": scenario.builtin,
        "created_at": scenario.created_at,
    }
