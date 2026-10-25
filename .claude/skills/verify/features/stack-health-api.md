# Stack health API

`GET /health` on the ingest API tells an operator whether AgentTrace can work. It answers 200 with every store `ok`, or 503 with `status: degraded` and the failing store's real error, and goes back to 200 once the store returns. The worker rides out the same outage and resumes consuming.

## Sub-features

- `health-ok` returns 200 and `{"status":"ok"}` with all four stores `ok`.
- `health-degraded` returns 503, names the stopped store with its error, and keeps the others `ok`.
- `health-recovery` returns to 200 after the store comes back.
- `health-worker-reconnect` keeps the worker running through a Redis restart, and it consumes new entries afterwards.

## How to get to it (user POV)

- `curl http://<host>:8000/health` (dev) or `:18000` (verify instance).
- The web health page, which renders this same response. See [web-health-page.md](./web-health-page.md).

## Driving it with agenttrace-verify

Preconditions:

- `H doctor` reports `DOCTOR OK`.

- **All ok.** Run `H health health-ok`. The body is `{"status":"ok","stores":{"postgres":"ok","clickhouse":"ok","redis":"ok","s3":"ok"}}`, followed by `http_status=200`.
- **One store down.** Stop Redis. Run `H store-stop redis`, then `H health health-redis-down`. The response shows `http_status=503` and `"status":"degraded"`, `redis` starts with `error: ConnectionError`, and postgres, clickhouse and s3 are still `ok`.
- **Each store.** Repeat the previous step for `postgres`, `clickhouse` and `seaweedfs` (that one reports as `s3`), using capture names `health-<svc>-down`. Only the stopped store shows `error: …`. Restart each one before the next with `H store-start <svc>`.
- **Recovery.** Run `H store-start redis`, then `H health health-recovered`. It prints `http_status=200` with all four stores `ok`.
- **Worker reconnect.** After the Redis restart, run `docker inspect -f '{{.State.Status}}' agenttrace-verify-worker-1` and `docker logs agenttrace-verify-worker-1 2>&1 | grep "redis unavailable" > verify-artifacts/<run-id>/worker-reconnect.log`. The status is `running`, and the log has at least one `redis unavailable (…); retrying in 1s` line.
- **Worker consumes again.** Run `docker exec agenttrace-verify-redis-1 redis-cli XADD spans '*' span '{}'`, wait 3 s, then `docker exec agenttrace-verify-redis-1 redis-cli XINFO GROUPS spans`. It shows `consumers` `1` and `pending` `0`, and `docker logs agenttrace-verify-worker-1` contains `received 1 span entries`.
- **Proof.** Keep `health-ok`, `health-redis-down`, `health-recovered`, `worker-reconnect.log` and the final `XINFO` output in the run dir. Finish with `H doctor`, which must report `DOCTOR OK`.

## Gotchas

- Stopping `ingest` itself makes `/health` unreachable (curl exits non-zero with an empty body). That case belongs to the web page's "unreachable" state, not to `health-degraded`.
- A down store adds latency. The ClickHouse and Postgres checks wait up to their 2 to 3 s timeouts, so `health` while a store is down takes a few seconds. Don't wrap it in a shorter timeout.
- With `seaweedfs` stopped, `s3` reads `error: TimeoutError: ` with an empty message, because boto3 keeps retrying past the 3 s health timeout. That's the expected degraded result, not a missing error.
- The Redis error text is `Name or service not known` while the container is stopped (Docker DNS drops the name). `Connection refused` appears instead if Redis is up but not listening. Both count as `error:`.
- Before the worker fix (commit `2211fbf`), the worker exited for good on a Redis restart. If `worker-reconnect` fails with `Exited (1)`, check that the build includes that fix.
