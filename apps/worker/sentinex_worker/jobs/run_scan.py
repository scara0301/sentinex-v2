import structlog
from ..orchestrator import ScanOrchestrator
from ..settings import worker_settings

log = structlog.get_logger()


async def run_scan(ctx, scan_id: str):
    docker_client = ctx.get("docker_client")
    redis = ctx.get("redis")
    if not docker_client:
        import docker
        docker_client = docker.DockerClient(base_url=worker_settings.docker_host)
    orchestrator = ScanOrchestrator(docker_client=docker_client, redis_client=redis)
    await orchestrator.run(scan_id)
