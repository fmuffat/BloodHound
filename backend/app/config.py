from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Graylog
    graylog_api_url: str = "http://graylog:9000/api"
    graylog_user: str = "admin"
    graylog_password: str = "admin"

    # Redis
    redis_url: str = "redis://redis:6379"
    opensearch_url: str = "http://opensearch:9200"

    # Ruckus One — Europe region
    ruckus_one_api_url: str = "https://api.eu.ruckus.cloud"
    ruckus_one_tenant_id: str = ""
    ruckus_one_region: str = "EU"
    ruckus_one_client_id: str = ""
    ruckus_one_client_secret: str = ""

    # Unleashed
    unleashed_ip: str = ""
    unleashed_username: str = "admin"
    unleashed_password: str = ""
    unleashed_enabled: bool = False

    # Worker intervals
    ruckus_sync_interval_minutes: int = 5
    unleashed_sync_interval_minutes: int = 5
    dns_resolve_interval_minutes: int = 5
    dns_cache_ttl_seconds: int = 3600
    dns_timeout_seconds: float = 2.0

    # SmartZone
    smartzone_host: str = ""
    smartzone_port: int = 8443
    smartzone_username: str = ""
    smartzone_password: str = ""
    smartzone_enabled: bool = False
    smartzone_sync_interval_minutes: int = 5

    class Config:
        env_file = ".env"


settings = Settings()
