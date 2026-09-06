import os
import socket
import uuid

import structlog
import docker
from arq.connections import RedisSettings
from sentinex_core.db.base import get_session, init_engine
from sentinex_core.db.repos import ScanRepo

from .jobs.run_scan import run_scan
from .jobs.render_report import render_report
from .jobs.apply_fix import apply_fix
from .settings import worker_settings

# Mid-flight statuses owned by a live orchestrator. PENDING is excluded — those
# scans are still queued and will be picked up normally.
_STALE_SCAN_STATUSES = (
    "PROVISIONING",
    "SEEDING",
    "RUNNING",
    "DRAINING",
    "SCORING",
    "REPORTING",
    "PAUSED",
)


# Identifies the containers and scans this worker process owns. Reaping and
# stale-scan failure are scoped to this id so that starting or restarting one
# worker cannot destroy sandboxes belonging to its peers.
WORKER_ID = os.environ.get("SENTINEX_WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


async def startup(ctx):
    init_engine(worker_settings.database_url)
    ctx["docker_client"] = docker.DockerClient(base_url=worker_settings.docker_host)
    ctx["worker_id"] = WORKER_ID
    structlog.get_logger().info("Worker started", worker_id=WORKER_ID)
    # Reap containers this same worker identity left behind on a previous run.
    await _reap_orphans(ctx["docker_client"])
    await _fail_stale_scans()


async def shutdown(ctx):
    ctx["docker_client"].close()


async def _reap_orphans(client: docker.DockerClient) -> None:
    """Remove sandbox containers/networks left behind by *this* worker.

    Filtering on sentinex.worker_id matters: an unfiltered sweep would
    force-remove every sandbox on the Docker host, including scans actively
    running under other worker containers — and running several workers is
    the documented way to scale past ``max_jobs``.
    """
    log = structlog.get_logger()
    owned = {"label": f"sentinex.worker_id={WORKER_ID}"}
    containers = client.containers.list(all=True, filters=owned)
    for c in containers:
        log.warning("Reaping orphaned container", container_id=c.id, name=c.name)
        try:
            c.remove(force=True)
        except Exception as e:
            log.error("Failed to reap container", error=str(e))
    networks = client.networks.list(filters=owned)
    for n in networks:
        try:
            n.remove()
        except Exception:
            pass


async def _fail_stale_scans() -> None:
    """Mark scans abandoned by a previous crash of *this* worker as FAILED.

    Scoped by the same worker identity as ``_reap_orphans``: a scan owned by
    a peer worker that is still running must not be marked failed here.
    """
    log = structlog.get_logger()
    try:
        async with get_session() as db:
            count = await ScanRepo(db).fail_stale(
                _STALE_SCAN_STATUSES, worker_id=WORKER_ID
            )
            await db.commit()
        if count:
            log.warning("Failed stale scans on startup", count=count)
    except Exception as e:
        log.error("Could not fail stale scans", error=str(e))


class WorkerSettings:
    functions = [run_scan, render_report, apply_fix]
    redis_settings = RedisSettings.from_dsn(worker_settings.redis_url)
    max_jobs = worker_settings.max_jobs
    job_timeout = worker_settings.scan_timeout_seconds + 60
    on_startup = startup
    on_shutdown = shutdown
