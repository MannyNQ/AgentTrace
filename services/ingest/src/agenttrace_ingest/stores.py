"""Clients for the four backing stores, and one readiness check per store."""

import boto3
import clickhouse_connect
import psycopg
import redis
from botocore.exceptions import ClientError

from agenttrace_ingest.settings import Settings


def s3_client(s: Settings):
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name="us-east-1",
    )


def ensure_bucket(s: Settings) -> None:
    """Create the payload bucket if it's missing. Safe to call on every startup."""
    client = s3_client(s)
    try:
        client.head_bucket(Bucket=s.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=s.s3_bucket)


def check_postgres(s: Settings) -> None:
    with psycopg.connect(s.postgres_dsn, connect_timeout=2) as conn:
        row = conn.execute("SELECT count(*) FROM projects").fetchone()
        assert row is not None and row[0] >= 1, "no default project"


def clickhouse_client(s: Settings, **options):
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_database,
        **options,
    )


def check_clickhouse(s: Settings) -> None:
    client = clickhouse_client(s, connect_timeout=2)
    assert client.command("EXISTS TABLE spans") == 1, "spans table missing"


def check_redis(s: Settings) -> None:
    assert redis.Redis.from_url(s.redis_url, socket_timeout=2).ping()


def check_s3(s: Settings) -> None:
    s3_client(s).head_bucket(Bucket=s.s3_bucket)


CHECKS = {
    "postgres": check_postgres,
    "clickhouse": check_clickhouse,
    "redis": check_redis,
    "s3": check_s3,
}
