import hashlib
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession

from sentinex_core.db.base import get_session
from sentinex_core.db.repos import WorkspaceRepo


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with get_session() as session:
        yield session


async def get_current_workspace(
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
):
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    repo = WorkspaceRepo(db)
    workspace = await repo.get_by_api_key(key_hash)
    if not workspace:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return workspace
