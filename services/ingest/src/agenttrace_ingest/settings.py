from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Read from environment variables. Defaults match docker-compose.yml for local runs."""

    postgres_dsn: str = "postgresql://agenttrace:agenttrace@localhost:5432/agenttrace"
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "agenttrace"
    clickhouse_password: str = "agenttrace"
    clickhouse_database: str = "agenttrace"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:8333"
    s3_access_key: str = "agenttrace"
    s3_secret_key: str = "agenttrace-secret"
    s3_bucket: str = "payloads"
    health_timeout_s: float = 3.0
