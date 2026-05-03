import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sentinex_core.db.base import init_engine
from .settings import settings
from .routes import workspaces, agents, scans, scenarios

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine(settings.database_url)
    logger.info("SENTINEX API started", env=settings.sentinex_env)
    yield
    logger.info("SENTINEX API shutting down")


app = FastAPI(
    title="SENTINEX v2 API",
    description="AI Agent Runtime Security Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspaces.router, prefix="/workspace", tags=["workspaces"])
app.include_router(agents.router, prefix="/workspace/{workspace_id}/agent", tags=["agents"])
app.include_router(scans.router, prefix="/workspace/{workspace_id}/scan", tags=["scans"])
app.include_router(scenarios.router, prefix="/scenarios", tags=["scenarios"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
