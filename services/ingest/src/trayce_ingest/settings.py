import ipaddress

from pydantic_settings import BaseSettings

PRIVATE_NETWORKS = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"


class Settings(BaseSettings):
    """Read from environment variables. Defaults match docker-compose.yml for local runs."""

    postgres_dsn: str = "postgresql://trayce:trayce@localhost:5432/trayce"
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_user: str = "trayce"
    clickhouse_password: str = "trayce"
    clickhouse_database: str = "trayce"
    redis_url: str = "redis://localhost:6379/0"
    s3_endpoint: str = "http://localhost:8333"
    s3_access_key: str = "trayce"
    s3_secret_key: str = "trayce-secret"
    s3_bucket: str = "payloads"
    health_timeout_s: float = 3.0
    # ADR-0017: the key from .env, registered for the default project on startup.
    trayce_api_key: str | None = None
    # ADR-0034: claims the install when set; otherwise ingest prints a token while unclaimed.
    trayce_setup_token: str | None = None
    # Where people open the web app. While it's local, ingest accepts the published dev key
    # (ADR-0033); empty counts as unset, like compose's `${VAR:-}`.
    trayce_public_url: str = ""

    @property
    def public_url(self) -> str:
        return self.trayce_public_url or "http://localhost:3000"

    # Peers whose Trayce-Client-IP header ingest believes (ADR-0041), as comma-separated
    # addresses or CIDR ranges. Empty counts as unset: loopback and private networks, where the
    # web app reaches ingest from.
    trayce_trusted_forwarders: str = ""

    @property
    def trusted_forwarders(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        nets = (self.trayce_trusted_forwarders or PRIVATE_NETWORKS).split(",")
        return [ipaddress.ip_network(n.strip()) for n in nets if n.strip()]

    max_spans_per_request: int = 1000
    max_request_bytes: int = 5 * 1024 * 1024
    stream_maxlen: int = 1_000_000  # ADR-0015, approximate
    # ADR-0004 / ADR-0013: a trace whose root never ended is complete after this much quiet.
    trace_quiet_period_s: float = 300.0
