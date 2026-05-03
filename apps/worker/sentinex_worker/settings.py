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


worker_settings = WorkerSettings()
