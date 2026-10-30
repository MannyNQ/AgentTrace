"""The price table as migrated into Postgres, applied to an OTLP span the way the worker does."""

import json
import time
import uuid
from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from stack import emit_spans, register_key

from trayce import Span, new_span_id, new_trace_id
from trayce.pricing import PRICES_SQL, PriceTable
from trayce_ingest.app import create_app
from trayce_ingest.settings import Settings
from trayce_ingest.stores import clickhouse_client

pytestmark = pytest.mark.integration


class Queue:
    def __init__(self):
        self.pushed: list[tuple[UUID, str]] = []

    async def push(self, project_id: UUID, spans_json: str) -> None:
        self.pushed.append((project_id, spans_json))


def test_an_openai_otlp_span_is_priced_from_the_migrated_table():
    s = Settings()
    key = f"test-{uuid.uuid4()}"
    q = Queue()
    trace_id, span_id = new_trace_id(), new_span_id()
    now_ns = time.time_ns()
    attrs = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "openai",
        "gen_ai.request.model": "gpt-6.1-sol",
        "gen_ai.response.model": "gpt-6.1-sol",
        "gen_ai.usage.input_tokens": 1_000_000,  # includes the cached tokens, per OTel
        "gen_ai.usage.cache_read.input_tokens": 400_000,
        "gen_ai.usage.output_tokens": 100_000,
    }
    body = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "chat gpt-6.1-sol",
                                "startTimeUnixNano": str(now_ns),
                                "endTimeUnixNano": str(now_ns + 1_000_000),
                                "attributes": [
                                    {"key": k, "value": {"intValue": str(v)}}
                                    if isinstance(v, int)
                                    else {"key": k, "value": {"stringValue": v}}
                                    for k, v in attrs.items()
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    with TestClient(create_app(s, queue=q)) as c:  # lifespan applies the migrations
        register_key(s, key)
        r = c.post(
            "/otlp/v1/traces",
            content=json.dumps(body),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200, r.text

    [(project_id, spans_json)] = q.pushed
    spans = TypeAdapter(list[Span]).validate_json(spans_json)
    with psycopg.connect(s.postgres_dsn) as conn:
        prices = PriceTable.from_rows(conn.execute(PRICES_SQL).fetchall())
    emit_spans(spans, project_id, prices)

    [(cost,)] = (
        clickhouse_client(s)
        .query(
            "SELECT cost_usd FROM spans WHERE trace_id = {t:FixedString(32)}",
            parameters={"t": trace_id},
        )
        .result_rows
    )
    # 600K uncached input at $2, 400K cache reads at $0.10, 100K output at $10 per MTok.
    assert cost == pytest.approx(0.6 * 2.00 + 0.4 * 0.10 + 0.1 * 10.00)
