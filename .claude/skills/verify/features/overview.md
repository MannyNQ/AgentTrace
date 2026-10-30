# Overview

A project opens on `/overview`, which answers "is my agent getting slower, pricier, or less reliable, and where?" over 24h, 7d, or 30d: tiles for traces, error rate, p95 latency, and cost per trace, each with its change from the previous period; volume and errors per bucket and by span kind; p50, p95, and p99 latency with the slowest tools and models; LLM spend by model with unpriced calls listed apart; and a sortable tools table. The numbers come from ingest's `/v1/analytics/{volume,latency,cost,tools,models}`, read from ClickHouse without `FINAL` and labeled approximate (ADR-0037). Buckets are the viewer's hours and days, from a zone cookie the browser sets. Only production traffic counts unless eval and replay runs are included. Every bucket, tile, and row links to the trace list with the same filter, so the two agree (ADR-0038).

## Sub-features

- `ov-api` `/v1/analytics/<part>?range=24h|7d|30d&tz=<IANA>` plus the trace filter (`tag`, `user_id`, `service_name`, `status`, `span_kind`, `span_name`, `span_model`, `span_error`, `show_evals`, `show_replays`): 422 for a bad range, an unknown zone, `since`/`until`, or an unknown parameter; 403 for an `ingest` key; 200 for a session or a `full` key.
- `ov-window` 24 hourly or 7 or 30 daily buckets in the viewer's zone, ending with the current one, stopped at the project's `retention_days` (`window.clamped`); a previous period only when all of it is within retention.
- `ov-numbers` running traces count toward volume only; error rate, latency, and cost per trace are over completed traces; unpriced LLM calls are counted apart, never as $0; tools and models need 20 calls to rank among the slowest.
- `ov-page` `/overview` with the tiles, charts, tables, range buttons, tag/user/service filters, and "Include eval and replay runs"; `/` and `/p/<slug>/` redirect to it.
- `ov-drill` each chart bucket, tile, span-kind row, tool, and model opens `/traces` filtered to it; the trace list shows the filter as removable chips.
- `ov-budget` the 30d overview of 1M traces and 20M spans loads in under 2 s.

## How to get to it (user POV)

- Sign in: `/` lands on `/p/default/overview`. "Overview" is first in the project nav.
- The range buttons and the filter form on the page; any chart bar or point, tile, or table row, to the trace list.
- The API: `curl "http://127.0.0.1:18000/v1/analytics/volume?range=7d&tz=Europe/Paris" -H "Authorization: Bearer $FULL"`.

## Driving it with trayce-verify

Preconditions:

- A healthy run, signed in (`verify-artifacts/.state/cookies.txt`). `$FULL` is the `.env` key. No model key needed.

- **Known numbers.** `TRAYCE_API_KEY=$FULL TRAYCE_ENDPOINT=http://127.0.0.1:18000 uv run python .claude/skills/verify/bin/overview_check.py http://127.0.0.1:18000 http://127.0.0.1:13000 "$FULL" verify-artifacts/.state/cookies.txt` sends two traces and an eval run through the SDK's exporter and two through OpenTelemetry's OTLP exporter, all under a service of their own, then prints ten `PASS` lines: 4 traces with 1 error (25%), span kinds `agent 4/1, llm 3/0, tool 3/1`, 5 traces with eval runs, p50 200 ms and p99 400 ms, cost $0.0156 ($0.0039 per trace) with one unpriced call to `not-a-real-model`, tools `lookup_order`, `refund` (1 error), `search_kb`; the page's tiles `4`, `25%`, `≥ $0.0039` and its unpriced note; and the error tile opening a trace list of 1 row with a `status error` chip. Exit 0.
- **Access and parameters.** With `$FULL`: `?range=90d`, `?tz=Mars/Olympus`, `?since=2026-10-01T00:00:00Z`, and `?ranges=24h` are each 422. With an `ingest` key (see `otlp.md` for making one) it's 403.
- **Page.** In the browser pane at `/overview`: the zone in "Approximate · times in <zone>" is the browser's after the first load. Click a volume bar: the trace list opens with `from …` and `before …` chips for that bucket. Click a tool's name in the Tools table: chips `has a tool span named <tool>`. Screenshot both. Check light and dark (`resize_window` `colorScheme`) and the mobile preset, with no horizontal scroll.
- **Budget.** `uv run python .claude/skills/verify/bin/overview_bench.py http://127.0.0.1:18000 http://127.0.0.1:13000 verify-artifacts/.state/cookies.txt` fills a project `bench-<hex>` with 1M traces and 20M spans over 30 days (a few minutes), then prints the seconds for each `/v1/analytics` part at `range=30d`, all five in parallel, the whole page, and a span-filtered read. Pass a smaller trace count as a fifth argument for a quick check.

## Gotchas

- The checker's traces are backdated ten minutes. Run it within a fresh instance's first 24 hours of clock, or not across a retention boundary.
- The browser pane hidden: the zone cookie isn't set until React hydrates, which needs a frame. Take a screenshot to force one; until then the page renders in UTC.
- Analytics aggregate without `FINAL` (ADR-0037): a span delivered twice and not yet deduplicated counts twice. The checker sends each span once.
- The bench writes straight into ClickHouse and Postgres on the verify ports. Never point it at the dev stack.
