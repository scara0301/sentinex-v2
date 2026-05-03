from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    scan_id: str = os.environ.get("SCAN_ID", "unknown")
    redis_url: str = "redis://localhost:6379/0"
    proxy_port: int = 8080
    injection_enabled: bool = False  # Sprint 3

proxy_settings = ProxySettings()
