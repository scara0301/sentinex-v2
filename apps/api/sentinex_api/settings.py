from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinex:sentinex@localhost:5432/sentinex"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "change-me"
    sentinex_env: str = "development"
    # Comma-separated list of allowed browser origins (the dashboard URL).
    cors_origins: str = "http://localhost:3000"
    debug: bool = False
    upload_dir: str = "/tmp/sentinex/uploads"
    report_dir: str = "/tmp/sentinex/reports"  # shared volume with the worker
    max_upload_size_mb: int = 50
    scan_timeout_seconds: int = 600
    max_events_per_scan: int = 100_000


settings = Settings()
