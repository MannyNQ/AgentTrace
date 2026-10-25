# Self-host startup

A user runs `docker compose up` on an empty machine and gets a working stack: six healthy services, a default org and project, the span table, the payload bucket, and a worker attached to the span stream.

## Sub-features

- `startup-healthy` brings all six services to healthy from empty volumes.
- `startup-default-project` seeds org `default` and project `default` with the fixed ID `00000000-0000-0000-0000-000000000001`.
- `startup-spans-table` creates `agenttrace.spans` as a `ReplacingMergeTree`, `agenttrace.traces` as an `AggregatingMergeTree`, and the `traces_mv` materialized view between them.
- `startup-ingest-tables` creates Postgres `api_keys` (with the `.env` key registered for the default project) and a seeded `model_prices`.
- `startup-payload-bucket` creates the `payloads` S3 bucket.
- `startup-worker-group` registers a consumer in the `workers` group on the `spans` stream.

## How to get to it (user POV)

- Run `docker compose up -d --wait` in the repo root, following the README quickstart.

## Driving it with agenttrace-verify

Preconditions:

- No active verify run, and the verify ports are free. `H up` checks both.
- Docker (OrbStack) is running.

- **Fresh start.** Launch from empty volumes. Run `H up`. It prints `READY run=<id> …` and exits 0, and `compose-up.log` in the run dir ends with every service `Healthy`.
- **All healthy.** Run `H doctor`. Six `container <svc>: healthy` lines are followed by `DOCTOR OK`.
- **Default project.** Read it back from Postgres. Run `docker exec agenttrace-verify-postgres-1 psql -U agenttrace -d agenttrace -tAc "select o.name||'/'||p.name||' '||p.id from projects p join orgs o on o.id=p.org_id" > verify-artifacts/<run-id>/startup-project.out`. The output is exactly `default/default 00000000-0000-0000-0000-000000000001`.
- **Spans table.** Run `docker exec agenttrace-verify-clickhouse-1 clickhouse-client --user agenttrace --password agenttrace -q "SELECT name, engine FROM system.tables WHERE database='agenttrace'" > verify-artifacts/<run-id>/startup-tables.out`. The output contains `spans	ReplacingMergeTree`, `traces	AggregatingMergeTree` and `traces_mv	MaterializedView`.
- **Keys and prices.** Run `docker exec agenttrace-verify-postgres-1 psql -U agenttrace -d agenttrace -tAc "select count(*) from api_keys where project_id='00000000-0000-0000-0000-000000000001'; select model, input_per_mtok, output_per_mtok from model_prices where model='claude-opus-5-5'" > verify-artifacts/<run-id>/startup-ingest-tables.out`. It prints `1`, then `claude-opus-5-5|4.000000|20.000000`.
- **Payload bucket.** Run `uv run --all-packages python -c "import boto3; c=boto3.client('s3', endpoint_url='http://127.0.0.1:18333', aws_access_key_id='agenttrace', aws_secret_access_key='agenttrace-secret', region_name='us-east-1'); print([b['Name'] for b in c.list_buckets()['Buckets']])" > verify-artifacts/<run-id>/startup-buckets.out`. The output is `['payloads']`.
- **Worker attached.** Run `docker exec agenttrace-verify-redis-1 redis-cli XINFO GROUPS spans > verify-artifacts/<run-id>/startup-worker-group.out`. It shows `name` `workers`, `consumers` `1`, and `pending` `0`.

## Gotchas

- The first `H up` on a machine builds images and pulls ClickHouse, which takes minutes. Later runs take about 25 s.
- `docker exec` names are `agenttrace-verify-<svc>-1`. Without the `-verify` you'd be reading the user's dev stack.
- A healthy ingest already implies the project row, table and bucket exist, because `/health` checks them. The read-backs are still required, since they prove the stored values, not just that something exists.
- The worker group can show `consumers 0` for about a second right after `up`, until the worker's first read. Re-run the `XINFO` once before calling it a failure.
