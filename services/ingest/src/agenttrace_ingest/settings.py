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
    # ADR-0017: the key from .env, registered for the default project on startup.
    agenttrace_api_key: str | None = None
    # ADR-0034: claims the install when set; otherwise ingest prints a token while unclaimed.
    agenttrace_setup_token: str | None = None
    # Where people open the web app. While it's local, ingest accepts the published dev key
    # (ADR-0033); empty counts as unset, like compose's `${VAR:-}`.
    agenttrace_public_url: str = ""

    @property
    def public_url(self) -> str:
        return self.agenttrace_public_url or "http://localhost:3000"

    max_spans_per_request: int = 1000
    max_request_bytes: int = 5 * 1024 * 1024
    stream_maxlen: int = 1_000_000  # ADR-0015, approximate
