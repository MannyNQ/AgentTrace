# Engineering log

Hard problems as they come up, what was tried, and what worked. This becomes the write-up and the interview stories.

## 2026-10-25: MinIO images disappeared

Phase 0's compose file pointed at `minio/minio`. Every tag now returns "access denied": MinIO stopped publishing community images. Switched to SeaweedFS's S3 gateway (ADR-0012). Because services only use the S3 API through boto3, the swap touched the compose file and one config file, nothing in application code. Lesson: depend on the protocol, not the product.

## 2026-10-25: ClickHouse "unhealthy" while serving fine

The ClickHouse container never passed its healthcheck, so the ingest service never started. The server was up: `wget http://127.0.0.1:8123/ping` returned `Ok.` But the container has no IPv6, so ClickHouse logged `Listen [::]:8123 failed` and bound IPv4 only, while Alpine's `/etc/hosts` resolves `localhost` to `::1` first. The healthcheck's `wget localhost` was refused. Fix: healthchecks use `127.0.0.1`. Lesson: "unhealthy" means the check failed, not the service; test the check itself first.

## 2026-10-25: Worker died for good when Redis restarted

Found by driving the stack, not by the tests: stopping and starting Redis left the worker container `Exited (1)`. `xreadgroup` raised `ConnectionError: Connection closed by server`, nothing caught it, and the service had no restart policy. Unit tests never restart Redis, so they couldn't see it. Fix: `run_forever` catches connection errors, backs off exponentially (1s, 2s, 4s, capped at 30s), and re-ensures the consumer group after reconnecting (Redis may come back without it). Ingest and worker also get `restart: unless-stopped` for crashes we haven't thought of. Lesson: a background consumer's main loop must treat its broker going away as normal weather.

## 2026-10-25: The trace summary view wouldn't create

ClickHouse refused `traces_mv` at startup (`ILLEGAL_AGGREGATION: min(start_time) AS start_time is found inside another aggregate function`). The view's output aliases have to match the `traces` columns, so `min(start_time) AS start_time` was required, but ClickHouse resolves a name to an alias before a column: every later `start_time` in the same SELECT, such as `max(coalesce(end_time, start_time))`, meant the aggregate. Fix: read from a subquery that renames the source columns (`start_time AS s_start`) and aggregate those. The web app's summary query uses aliases that differ from the column names for the same reason. Lesson: in ClickHouse, an alias that matches a column name shadows it everywhere in that SELECT.

## 2026-10-25: A dead OR branch still runs

The trace list's first page failed with `Cannot read DateTime`. The cursor filter was `{before} = '' OR t_start < parseDateTime64BestEffort({before})`, and ClickHouse folds constants before it short-circuits, so it parsed `''` anyway. Fix: no special case in SQL; the first page passes a far-future cursor. Lesson: don't rely on short-circuit evaluation to guard a parse of a query parameter.

## 2026-10-25: A store outage must not spend an entry's retries

The plan was at-least-once delivery with a dead-letter after 5 deliveries (ADR-0015). Done naively, a ClickHouse outage dead-letters everything: each failed batch stays pending, gets reclaimed after 60 s idle, and its delivery count climbs to 5 in a few minutes. The worker now tells store outages (connection errors) apart from batches a store rejects. On an outage it holds the batches it has and retries them with backoff, without reading or reclaiming, so the delivery count doesn't move. Only real rejections are left pending, to be reclaimed and eventually dead-lettered. Verified by stopping ClickHouse mid-ingest: the pending entry stayed at 1 delivery, and all spans landed after the restart.

## 2026-10-25: A 413 that looked like a network error

A pre-PR review found that a burst of large LLM spans never reached ingest. The exporter batched by count (100 spans), ingest refuses bodies over 5 MB, and 100 spans with 60 KB conversations is 6 MB. Worse, the SDK never saw the 413: ingest rejects on `Content-Length` and closes the connection while the client is still sending, so urllib raised `BrokenPipeError`, which looked like a network blip and got retried three times before the whole batch was dropped. Fix: batches are bounded by bytes (4 MB) as well as count, and a single span too big for any request is sent with its large input or output cut to a prefix and marked `agenttrace.truncated`. The same review found that the exit-time flush retried with backoff, so a down server held a 500-span process open for 18 s; shutdown now gets one attempt per batch, a 2 s deadline, and wakes any in-flight retry. Lesson: a server that rejects early can make an HTTP error look like a transport error, so cap the request on the client.

## 2026-10-26: A trace pinned "forever" would have expired in 2069

The plan for pinning (ADR-0021) was to set `retention_days` to 65535, the UInt16 maximum, about 179 years. The integration test checked the expiry ClickHouse computes for a pinned row, `toDateTime(start_time) + toIntervalDay(retention_days)`, expecting something past 2200, and got 2070-02-21. `DateTime` is seconds since 1970 in a UInt32, so it ends on 2106-02-07, and adding past that wraps around silently: `toDateTime('2106-02-07 06:28:15') + toIntervalDay(1)` is `1970-01-01 23:59:59`. Every pinned trace would have quietly expired in 2069. Fix: pin by setting each row's `retention_days` to the days from its own start to 2106-01-01, so the sum lands just inside the range. The test now asserts the year is 2106. Lesson: "the type's maximum" means forever only if every expression it feeds into holds it too.

An earlier version of the same test inserted traces that were already past a 1-day retention and expected only the pinned one to survive. It failed about half the time, because a regular background merge applies TTL too (`SYSTEM STOP TTL MERGES` doesn't stop it) and could drop the row before the mutation ran. Pinning an expired trace isn't a real case, since "Add to dataset" needs the trace to exist, so the test now checks the computed expiry of fresh traces instead.
