import structlog
import docker
from arq.connections import RedisSettings
from sentinex_core.db.base import init_engine

from .jobs.run_scan import run_scan
from .jobs.render_report import render_report
from .jobs.apply_fix import apply_fix
from .settings import worker_settings


async def startup(ctx):
    init_engine(worker_settings.database_url)
    ctx["docker_client"] = docker.DockerClient(base_url=worker_settings.docker_host)
    structlog.get_logger().info("Worker started")
    # Reap orphaned scan containers from previous crashes
    await _reap_orphans(ctx["docker_client"])


async def shutdown(ctx):
    ctx["docker_client"].close()


async def _reap_orphans(client: docker.DockerClient) -> None:
    log = structlog.get_logger()
    containers = client.containers.list(all=True, filters={"label": "sentinex.scan_id"})
    for c in containers:
        log.warning("Reaping orphaned container", container_id=c.id, name=c.name)
        try:
            c.remove(force=True)
        except Exception as e:
            log.error("Failed to reap container", error=str(e))
    networks = client.networks.list(filters={"label": "sentinex.scan_id"})
    for n in networks:
        try:
            n.remove()
        except Exception:
            pass


class WorkerSettings:
    functions = [run_scan, render_report, apply_fix]
    redis_settings = RedisSettings.from_dsn(worker_settings.redis_url)
    max_jobs = 5
    job_timeout = worker_settings.scan_timeout_seconds + 60
    on_startup = startup
    on_shutdown = shutdown
