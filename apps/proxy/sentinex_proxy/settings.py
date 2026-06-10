import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    scan_id: str = os.environ.get("SCAN_ID", "unknown")
    redis_url: str = "redis://localhost:6379/0"
    proxy_port: int = 8080
    # JSON map of provider host fragment -> "mock-host:port". Requests whose
    # host matches a fragment are transparently rerouted to the mock service
    # on the sandbox network, e.g. {"stripe.com": "sx-<scan>-mock-stripe:4010"}.
    mock_host_map: str = "{}"


proxy_settings = ProxySettings()
