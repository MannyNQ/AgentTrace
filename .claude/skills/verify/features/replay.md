# Replay

`agenttrace replay <trace_id>` re-runs a recorded trace's root input through the user's entry point on their machine, `--samples` times (ADR-0028). It reads the input from `GET /v1/traces/<id>/input` and uploads a replay to `/v1/replays` (ADR-0029). Each replay run is its own trace whose root span, `agenttrace.replay.run`, carries `agenttrace.replay_of` = `<trace_id>:<span_id>` of the original's root span. ClickHouse's `traces.replay_of` holds the original trace ID, filled by `traces_mv` through the ClickHouse migration `0001_replay_of.sql`. When `eval.yaml`'s dataset has a draft item made from the original and that item has an expectation, the runs are scored with `eval.yaml`'s scorers against that draft revision (ADR-0030). The web app hides replay runs from the trace list by default, lists a trace's replays on its page, and shows each replay at `/replays/<id>` with the original and the runs side by side and each run's first difference marked.

## Sub-features

- `rp-migrate` ingest applies `migrations/clickhouse/NNNN_*.sql` on startup, recorded in ClickHouse's `schema_migrations`; `0001_replay_of.sql` adds `traces.replay_of` and swaps `traces_mv`'s query with `MODIFY QUERY`.
- `rp-input` `GET /v1/traces/<id>/input` returns `trace_id`, `span_id`, the full `input` (from S3 when offloaded), and `replay_of` (null unless the trace is a replay run). A truncated input is 422, an unknown trace 404, a malformed ID 404.
- `rp-api` `/v1/replays`: create (retry 200, other settings 409; a scored replay needs `item_id`, `item_rev`, and scorers together, and the item must be made from `replay_of.trace_id`), upload runs by `run_index` (409 once finished), finish (`completed` needs every run), `GET /v1/replays?trace_id=` newest first, and `GET /v1/replays/<id>` with `item` and `replay_runs`. Pass counts, cost, and `errored` come back on every replay.
- `rp-cli` `agenttrace replay <trace> [-c eval.yaml] [--entry m:f] [--samples k] [--require-pass] [--web-url]`: one line per run, then pass counts and the replay page link. Exits 0, 1 (failed), 2 (couldn't start, nothing recorded), 3 (`--require-pass` and a scored run failed). `--require-pass` with nothing to score exits 2 before running. Replaying a replay run says `is a replay run; replaying its original`. Records commit, branch, and `git_dirty`.
- `rp-ui-list` `/traces` hides replay runs; "Show replays" (`?replays=1`) shows them with a `replay` label; "Older →" keeps the toggle.
- `rp-ui-trace` a trace page has a "Replays" section (each replay's time, status, code with `+ uncommitted changes`, runs, pass counts or `unscored`) and a "Replay" disclosure with `agenttrace replay <id>`. A replay run's page instead says "A replay run of trace …" linking the original.
- `rp-ui-page` `/replays/<id>`: status, code, runs, cost, the item revision it was scored against, pass counts, the item's expected tool calls, the original's output and tool calls, and each run's scores with reasons, output, and tool calls with "same as the original" or the first difference outlined. An expired original says so.

## How to get to it (user POV)

- The CLI: `uv run agenttrace replay <trace_id>` where the agent's `eval.yaml` lives, with `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` set.
- The web app: a trace page → "Replay" for the command, "Replays" → a replay's time → the replay page; `/traces` → "Show replays".
- The API: `curl -H "Authorization: Bearer $AGENTTRACE_API_KEY" "http://127.0.0.1:18000/v1/replays?trace_id=<id>"`.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run. No model key: the journey uses the scripted agent in `.github/eval-gate/`. Below, `ENV` is `AGENTTRACE_API_KEY="$KEY" AGENTTRACE_ENDPOINT=http://127.0.0.1:18000 AGENTTRACE_WEB_URL=http://127.0.0.1:13000`, and `$S` is a scratch directory holding a copy of `.github/eval-gate/gate_agent.py` and an `eval.yaml` with `entry: gate_agent:entry`, `dataset: replay-demo`, `samples: 3`, and one scorer `{name: steps, type: trajectory, mode: in_order}`.

- **Migration.** `H ch rp "SELECT name FROM schema_migrations"` lists `0001_replay_of.sql`.
- **Record the failure.** `EVAL_GATE_BROKEN=1 ENV uv run python .claude/skills/verify/bin/replay_original.py "Can I get a refund for order 1043?"` prints `$T`. `curl … /v1/traces/$T/input` returns `{"question": …}` and `"replay_of": null`.
- **Add to dataset.** `POST /v1/datasets` with `{"name": "replay-demo", "trace_id": "$T"}`; the draft has one item with `needs_expectation: true`.
- **Nothing to score.** From `$S`, `ENV uv run --project <repo> agenttrace replay $T --require-pass` exits 2 with `--require-pass needs something to score, but item … has no expected output or trajectory yet`. Without the flag it runs 3 runs `unscored` and exits 0.
- **Still broken.** PATCH the item to `expected_trajectory: [{tool: lookup_order}, {tool: issue_refund}]` (rev 2). `EVAL_GATE_BROKEN=1 … agenttrace replay $T --require-pass`: `scored against item … rev 2`, three `steps:FAIL` lines, `steps  0/3  FAIL`, exit 3.
- **Fixed.** The same without `EVAL_GATE_BROKEN`: `steps  3/3  pass`, exit 0, and the replay page link.
- **Replay of a replay.** Replay one of the fixed replay's run traces with `--samples 1`: `… is a replay run; replaying its original`, and the new replay is listed under `$T`. `H ch rp2 "SELECT count() FROM (SELECT trace_id, max(replay_of) AS r FROM traces GROUP BY trace_id) WHERE r = '$T'"` counts every run (10 after the steps above).
- **Git.** From the repo root with uncommitted changes, `-c $S/eval.yaml --samples 1`: the newest replay has the branch, commit, and `git_dirty: true`; the trace page shows `+ uncommitted changes`.
- **UI.** `/traces` hides every replay run (compare the row count with `/traces?replays=1`, where they carry `replay`). `/traces/$T` lists the replays newest first with `steps 0/3` in red and `steps 3/3` in green, and the "Replay" disclosure shows `agenttrace replay $T`. The broken replay's page shows every run "same as the original" (no tool calls); the fixed one marks `lookup_order` as the first difference at step 1 on every run. A run trace's page says "A replay run of trace …".
- **Integration tests.** The datasets entry's env, then `uv run pytest services/ingest -m integration` includes the ClickHouse migration, `replay_of` on `traces`, the input endpoint with offloaded and truncated input, and a scored replay in Postgres.

## Gotchas

- Tests that read `FixedString` columns through clickhouse-connect get bytes; select `toString(trace_id)`.
- `git_dirty` and the commit are null when the CLI runs outside a git work tree, and the UI says "unknown code".
- The replay page reads the original from ClickHouse: once it expires, there's no first difference to mark. Replay runs keep their own trajectories in Postgres.
- A replay run's tool arguments are stored cut to 4 KB each, so an argument larger than that differs from the original's full one.
