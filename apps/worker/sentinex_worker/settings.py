from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinex:sentinex@localhost:5432/sentinex"
    redis_url: str = "redis://localhost:6379/0"
    docker_host: str = "unix:///var/run/docker.sock"
    sandbox_image_base: str = "sentinex/sandbox-langchain:latest"
    sandbox_mem_limit: str = "2g"
    sandbox_cpu_quota: int = 100_000   # 1 CPU = 100000
    sandbox_pids_limit: int = 256
    scan_timeout_seconds: int = 600
    max_events_per_scan: int = 100_000
    proxy_port: int = 8080
    mock_db_image: str = "sentinex/mock-db:latest"  # falls back to postgres:16-alpine
    # Extra (non-internal) network the proxy joins so it can reach Redis.
    # On the dev compose stack this is the "sentinex" network.
    proxy_egress_network: str = ""
    report_dir: str = "/tmp/sentinex/reports"
    # When the worker runs inside a container, agent bundle paths recorded by
    # the API (e.g. /data/uploads/...) are container paths. Docker bind mounts
    # resolve on the *host*, so the orchestrator rewrites this prefix:
    uploads_container_dir: str = ""
    uploads_host_dir: str = ""


worker_settings = WorkerSettings()
