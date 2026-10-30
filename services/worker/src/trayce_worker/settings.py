import socket

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from environment variables. Defaults match docker-compose.yml for local runs."""

    redis_url: str = "redis://localhost:6379/0"
    postgres_dsn: str = "postgresql://trayce:trayce@localhost:5432/trayce"
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "trayce"
    clickhouse_password: str = "trayce"
    clickhouse_database: str = "trayce"
    s3_endpoint: str = "http://localhost:8333"
    s3_access_key: str = "trayce"
    s3_secret_key: str = "trayce-secret"
    s3_bucket: str = "payloads"

    consumer_name: str = socket.gethostname()
    batch_size: int = 50  # stream entries per read; each entry is one ingest request
    block_ms: int = 1000
    reclaim_idle_ms: int = 60_000  # ADR-0015
    max_deliveries: int = 5
    dedup_ttl_s: int = 3600  # ADR-0014
    cache_ttl_s: float = 60.0  # prices and project retention
    default_retention_days: int = 30
    payload_offload_bytes: int = 8 * 1024  # ADR-0009
    payload_preview_bytes: int = 1024
