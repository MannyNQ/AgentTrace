"""Clients for the four backing stores, and one readiness check per store."""

from collections.abc import Sequence
from functools import cached_property
from typing import Any
from uuid import UUID

import boto3
import clickhouse_connect
import psycopg
import redis
from botocore.exceptions import ClientError

from trayce_ingest.settings import Settings


def s3_client(s: Settings):
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name="us-east-1",
    )


def read_payload(s: Settings, client, project: UUID, uri: str) -> str:
    """An offloaded payload (ADR-0009). `uri` comes from a span row, never from a request, and
    the worker keys each payload under its project, so one outside `project` is refused (#32)."""
    prefix = f"s3://{s.s3_bucket}/{project}/"
    if not uri.startswith(prefix):
        raise RuntimeError(f"payload uri outside project {project}: {uri}")
    key = uri[len(f"s3://{s.s3_bucket}/") :]
    return client.get_object(Bucket=s.s3_bucket, Key=key)["Body"].read().decode()


PROJECT_FILTER = "project_id = {project:UUID}"


class ProjectReads:
    """ClickHouse reads and mutations for one project at a time (#32). Each binds `project`
    itself, and SQL that doesn't filter on `PROJECT_FILTER` is refused: left out, a query would
    read every project's rows. Built lazily, since the client connects when built and the app
    builds its stores at import."""

    def __init__(self, s: Settings):
        self.s = s

    @cached_property
    def client(self):
        return clickhouse_client(self.s)

    def query(self, project: UUID, sql: str, parameters: dict | None = None) -> Sequence[Any]:
        return self.client.query(sql, parameters=self._bind(project, sql, parameters)).result_rows

    def command(self, project: UUID, sql: str, parameters: dict | None = None) -> None:
        self.client.command(sql, parameters=self._bind(project, sql, parameters))

    @staticmethod
    def _bind(project: UUID, sql: str, parameters: dict | None) -> dict:
        if PROJECT_FILTER not in sql:
            raise ValueError(f"a project read must filter on {PROJECT_FILTER}: {sql[:80]}")
        return {**(parameters or {}), "project": str(project)}


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
    # No ClickHouse session: request threads share each client, and a session refuses concurrent
    # queries. Nothing here keeps session state (temporary tables, SET).
    return clickhouse_connect.get_client(
        host=s.clickhouse_host,
        port=s.clickhouse_port,
        username=s.clickhouse_user,
        password=s.clickhouse_password,
        database=s.clickhouse_database,
        autogenerate_session_id=False,
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
