# 15. Redis Streams: at-least-once, reclaim, dead-letter, capped length

Status: accepted

## Context

Ingest returns once spans are in Redis; workers move them to ClickHouse. A worker can crash with entries delivered but not acknowledged, a malformed entry can fail forever, and an outage of ClickHouse must not grow the stream without bound.

## Decision

- One stream entry holds one ingest batch, not one span.
- Workers read through the `workers` consumer group and acknowledge only after the batch is stored (at-least-once).
- Each loop reclaims entries idle for over 60 seconds with `XAUTOCLAIM`.
- An entry delivered more than 5 times moves to the `spans:dead` stream and is acknowledged. An entry that can't be parsed moves there at once, since retrying can't fix it.
- While a store is down, a worker holds the batches it has and retries them with backoff instead of reading more, so an outage doesn't count toward an entry's deliveries.
- Ingest appends with `MAXLEN ~ 1000000`; past that, the oldest entries are dropped.
- Workers delete an entry when they acknowledge it, so the stream holds only batches not yet stored. Redis runs with `maxmemory` (`AGENTTRACE_REDIS_MAXMEMORY`, default 1 GB) and `noeviction`: at the cap, ingest answers span batches with 503 instead of the backlog taking the host's memory.

## Consequences

- Duplicates are expected and handled by ADR-0014.
- A long ClickHouse outage beyond about a million batches loses the oldest spans.
- A long ClickHouse outage, or a flood of large batches, fills Redis's memory cap first; SDKs then get 503 and retry or drop, and sign-in, whose failure counts live in the same Redis, fails until the workers catch up.
- `spans:dead` needs a human to inspect; nothing replays it yet.
