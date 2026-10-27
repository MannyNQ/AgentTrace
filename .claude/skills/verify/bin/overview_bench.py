"""The overview's load budget (design.md, Phase 8): 30 days of 1M traces and 20M spans in one
project, and how long the 30d overview takes to read. Fills a project of its own, `bench`, straight
into ClickHouse's `spans` (the materialized view fills `traces` as usual), then times every
`/v1/analytics` part over HTTP with that project's key, and the whole page as the signed-in owner.

    uv run python .claude/skills/verify/bin/overview_bench.py <ingest> <web> <cookie jar> [traces]

Verify ports only: it writes to Postgres on :15432 and ClickHouse on :18123.
"""

import hashlib
import json
import secrets
import statistics
import sys
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import clickhouse_connect
import psycopg
from web_session import cookie_header

ingest, web, jar_path = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/"), sys.argv[3]
TRACES = int(sys.argv[4]) if len(sys.argv) > 4 else 1_000_000
SPANS_PER_TRACE = 20
CHUNK = 2_000_000  # spans per INSERT
DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"
RUNS = 3

project, slug = uuid.uuid4(), f"bench-{secrets.token_hex(3)}"
key = f"bench-{secrets.token_hex(16)}"
with psycopg.connect("postgresql://agenttrace:agenttrace@127.0.0.1:15432/agenttrace") as conn:
    conn.execute(
        "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, %s, %s)",
        (project, DEFAULT_ORG, slug, slug),
    )
    conn.execute(
        "INSERT INTO api_keys (key_hash, project_id, name, scope, hint)"
        " VALUES (%s, %s, %s, %s, %s)",
        (hashlib.sha256(key.encode()).hexdigest(), project, "bench", "full", key[-4:]),
    )

ch = clickhouse_connect.get_client(
    host="127.0.0.1", port=18123, username="agenttrace", password="agenttrace",
    database="agenttrace", autogenerate_session_id=False,
)  # fmt: skip
# Span n belongs to trace n / 20; its first span is the root. Traces start uniformly over 30 days.
# About 3% of spans fail; odd spans are LLM calls on four models, even ones tool calls on eight.
FILL = """
INSERT INTO spans (project_id, trace_id, span_id, parent_span_id, name, kind, start_time, end_time,
                   status, model, input_tokens, output_tokens, cost_usd, user_id, tags, attributes,
                   retention_days)
SELECT
    {project:UUID},
    toFixedString(lower(hex(MD5(toString(t)))), 32),
    toFixedString(substring(lower(hex(MD5(concat(toString(n), 's')))), 1, 16), 16),
    if(i = 0, '', substring(lower(hex(MD5(concat(toString(t * 20), 's')))), 1, 16)),
    multiIf(i = 0, 'bench.run', i % 2 = 1, 'anthropic.messages.create',
            ['lookup_order', 'search_kb', 'refund', 'send_email', 'get_weather', 'book', 'cancel',
             'escalate'][cityHash64(n, 3) % 8 + 1]),
    multiIf(i = 0, 'agent', i % 2 = 1, 'llm', 'tool'),
    start,
    start + toIntervalMillisecond(if(i = 0, 2000 + cityHash64(t, 1) % 3000, cityHash64(n) % 500)),
    if(cityHash64(n, 2) % 100 < 3, 'error', 'ok'),
    if(i % 2 = 1, ['claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'in-house'][
        cityHash64(n, 5) % 4 + 1], ''),
    if(i % 2 = 1, toUInt32(cityHash64(n, 4) % 4000), NULL),
    if(i % 2 = 1, toUInt32(cityHash64(n, 6) % 800), NULL),
    if(i % 2 = 1, (cityHash64(n, 4) % 4000) * 3e-6 + (cityHash64(n, 6) % 800) * 1.5e-5, NULL),
    if(i = 0, concat('u', toString(cityHash64(t) % 1000)), ''),
    if(i = 0, [['vip', 'eu', 'us'][cityHash64(t, 7) % 3 + 1]], []),
    if(i = 0, map('resource.service.name', 'bench-svc'), map()),
    30
FROM (
    SELECT number AS n, intDiv(number, 20) AS t, number % 20 AS i,
           now64(6) - toIntervalMillisecond(cityHash64(intDiv(number, 20)) % (30 * 86400000))
             + toIntervalMillisecond((number % 20) * 10) AS start
    FROM numbers({offset:UInt64}, {count:UInt64})
)"""
total = TRACES * SPANS_PER_TRACE
began = time.monotonic()
for offset in range(0, total, CHUNK):
    ch.command(FILL, parameters={"project": str(project), "offset": offset,
                                 "count": min(CHUNK, total - offset)})  # fmt: skip
    print(f"filled {min(offset + CHUNK, total):,} / {total:,} spans", file=sys.stderr)
fill_s = time.monotonic() - began
stored = ch.query(
    "SELECT uniqExact(trace_id), count() FROM spans WHERE project_id = {p:UUID}",
    parameters={"p": str(project)},
).result_rows[0]

cookies = cookie_header(jar_path)


def timed(url: str, headers: dict) -> float:
    began = time.monotonic()
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        r.read()
    return time.monotonic() - began


auth = {"Authorization": f"Bearer {key}"}
parts = ["volume", "latency", "cost", "tools", "models"]
results: dict[str, list[float]] = {}
for part in parts:
    results[part] = [timed(f"{ingest}/v1/analytics/{part}?range=30d", auth) for _ in range(RUNS)]


def parallel() -> float:
    """All five parts at once, as the page's panels read them."""
    began = time.monotonic()
    with ThreadPoolExecutor(len(parts)) as pool:
        list(pool.map(lambda p: timed(f"{ingest}/v1/analytics/{p}?range=30d", auth), parts))
    return time.monotonic() - began


results["all parts in parallel"] = [parallel() for _ in range(RUNS)]
page = (
    urllib.request.urlopen(
        urllib.request.Request(f"{web}/p/{slug}/overview?range=30d", headers={"Cookie": cookies})
    )
    .read()
    .decode()
)
# A fast error panel isn't a fast page.
assert "data-tile-value" in page and "data-panel-error" not in page, "no numbers on the page"
results["page /overview?range=30d"] = [
    timed(f"{web}/p/{slug}/overview?range=30d", {"Cookie": cookies}) for _ in range(RUNS)
]
results["filtered: tools of traces with an errored refund"] = [
    timed(f"{ingest}/v1/analytics/tools?range=30d&span_name=refund&span_error=true", auth)
    for _ in range(RUNS)
]

report = {
    "project": slug,
    "traces": stored[0],
    "spans": stored[1],
    "fill_s": round(fill_s, 1),
    "seconds": {k: [round(x, 3) for x in v] for k, v in results.items()},
    "median_s": {k: round(statistics.median(v), 3) for k, v in results.items()},
}
print(json.dumps(report, indent=2))
