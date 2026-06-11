import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from sentinex_core.db.repos import WorkspaceRepo
from ..deps import get_db, get_current_workspace

router = APIRouter()


class WorkspaceCreate(BaseModel):
    name: str


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    api_key: str  # only returned on creation


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(body: WorkspaceCreate, db: AsyncSession = Depends(get_db)):
    api_key = f"sx-{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    repo = WorkspaceRepo(db)
    ws = await repo.create(name=body.name, api_key_hash=key_hash)
    await db.commit()
    return WorkspaceResponse(id=ws.id, name=ws.name, api_key=api_key)


@router.get("/{workspace_id}")
async def get_workspace(
    workspace_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    workspace=Depends(get_current_workspace),
):
    # The API key authenticates a single workspace; you may only read your own.
    if workspace.id != workspace_id:
        raise HTTPException(404, "Workspace not found")
    return {
        "id": workspace.id,
        "name": workspace.name,
        "plan": workspace.plan,
        "created_at": workspace.created_at,
    }
