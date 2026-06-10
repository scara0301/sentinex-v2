import structlog
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sentinex_core.db.base import get_session, init_engine
from sentinex_core.db.repos import ScenarioRepo
from sentinex_core.scenarios import iter_builtin_scenarios
from .settings import settings
from .routes import workspaces, agents, scans, scenarios, badges, billing
from .routes import scans_ws
from .ws.manager import ConnectionManager
from .ws.fanout import RedisFanout

logger = structlog.get_logger()


async def _seed_builtin_scenarios() -> None:
    """Upsert packaged builtin scenarios so they are selectable by ID."""
    try:
        async with get_session() as db:
            repo = ScenarioRepo(db)
            for yaml_text, spec in iter_builtin_scenarios():
                existing = await repo.get_builtin_by_slug(spec.slug)
                if existing:
                    await repo.update_definition(
                        existing.id,
                        name=spec.name,
                        description=spec.description,
                        yaml_dsl=yaml_text,
                        parsed=spec.model_dump(mode="json"),
                        tags=spec.tags,
                    )
                else:
                    await repo.create(
                        workspace_id=None,
                        name=spec.name,
                        slug=spec.slug,
                        description=spec.description,
                        yaml_dsl=yaml_text,
                        parsed=spec.model_dump(mode="json"),
                        tags=spec.tags,
                        builtin=True,
                    )
            await db.commit()
        logger.info("Builtin scenarios seeded")
    except Exception as exc:
        # Don't block startup (e.g. migrations not yet applied).
        logger.warning("Builtin scenario seeding failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_engine(settings.database_url)
    await _seed_builtin_scenarios()

    arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.arq_pool = arq_pool

    manager = ConnectionManager()
    fanout = RedisFanout(redis_url=settings.redis_url, manager=manager)
    app.state.ws_manager = manager
    app.state.ws_fanout = fanout

    logger.info("SENTINEX API started", env=settings.sentinex_env)
    yield

    await arq_pool.close()
    await fanout.shutdown()
    logger.info("SENTINEX API shutting down")


app = FastAPI(
    title="SENTINEX v2 API",
    description="AI Agent Runtime Security Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspaces.router, prefix="/workspace", tags=["workspaces"])
app.include_router(agents.router, prefix="/workspace/{workspace_id}/agent", tags=["agents"])
app.include_router(scans.router, prefix="/workspace/{workspace_id}/scan", tags=["scans"])
app.include_router(scenarios.router, prefix="/scenarios", tags=["scenarios"])
app.include_router(scans_ws.router, tags=["scans-ws"])
app.include_router(badges.router, tags=["badges"])
app.include_router(billing.router, prefix="/workspace/{workspace_id}", tags=["billing"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
