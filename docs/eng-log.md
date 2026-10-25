# Engineering log

Hard problems as they come up, what was tried, and what worked. This becomes the write-up and the interview stories.

## 2026-10-25: MinIO images disappeared

Phase 0's compose file pointed at `minio/minio`. Every tag now returns "access denied": MinIO stopped publishing community images. Switched to SeaweedFS's S3 gateway (ADR-0012). Because services only use the S3 API through boto3, the swap touched the compose file and one config file, nothing in application code. Lesson: depend on the protocol, not the product.

## 2026-10-25: ClickHouse "unhealthy" while serving fine

The ClickHouse container never passed its healthcheck, so the ingest service never started. The server was up: `wget http://127.0.0.1:8123/ping` returned `Ok.` But the container has no IPv6, so ClickHouse logged `Listen [::]:8123 failed` and bound IPv4 only, while Alpine's `/etc/hosts` resolves `localhost` to `::1` first. The healthcheck's `wget localhost` was refused. Fix: healthchecks use `127.0.0.1`. Lesson: "unhealthy" means the check failed, not the service; test the check itself first.

## 2026-10-25: Worker died for good when Redis restarted

Found by driving the stack, not by the tests: stopping and starting Redis left the worker container `Exited (1)`. `xreadgroup` raised `ConnectionError: Connection closed by server`, nothing caught it, and the service had no restart policy. Unit tests never restart Redis, so they couldn't see it. Fix: `run_forever` catches connection errors, backs off exponentially (1s, 2s, 4s, capped at 30s), and re-ensures the consumer group after reconnecting (Redis may come back without it). Ingest and worker also get `restart: unless-stopped` for crashes we haven't thought of. Lesson: a background consumer's main loop must treat its broker going away as normal weather.
