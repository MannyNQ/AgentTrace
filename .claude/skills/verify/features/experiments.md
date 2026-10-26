# Experiments

The eval runner uploads results to `/v1/experiments` in the ingest API (ADR-0023), with the same Bearer keys as `/v1/datasets`. An experiment pins a published dataset version and a list of scorers. The runner creates it as `running` with an ID it generated, streams samples with their scores, and finishes it as `completed` (every item × sample uploaded) or `failed`. The server prices each sample from the price table, computes pass/fail from each scorer's threshold, and summarizes pass rates per scorer and per item. `agenttrace eval` (the `agenttrace` package's CLI) is the runner: it reads `eval.yaml`, runs the entry point over every item `samples` times under its own root span, scores the sample from the trace's tool spans and the output, and uploads. The web app shows them at `/experiments`.

## Sub-features

- `ex-create` creates an experiment on a published version; a retried create (same ID and settings) is 200 with the same experiment; the same ID with other settings is 409; an unknown version is 404.
- `ex-upload` upserts samples by `(item_id, sample_index)`, so a retried upload changes nothing; refuses items not in the version, `sample_index` at or past `samples`, unknown scorers, duplicate samples in one batch, outputs over 1 MB, and NUL characters.
- `ex-finish` `completed` needs every sample and says how many are missing; `failed` is allowed any time while running; a finished experiment refuses uploads (409); a repeated finish with the same status succeeds.
- `ex-cost` each sample is priced on upload from its per-model token counts at its `started_at`; an unknown model makes that sample's cost null and counts it under `unpriced_samples`.
- `ex-summary` `GET /v1/experiments/<id>` has per-scorer `scored`, `passed`, `pass_rate`, `mean_score`, and `pass_rate_by_sample`, and per-item pass counts and errored samples, in dataset order. Items lacking a scorer's expectation aren't counted for it.
- `ex-item` `GET /v1/experiments/<id>/items/<item_id>` has the item as the version holds it and every sample's output, trajectory, error, scores, and cost.
- `ex-ui-list` `/experiments` lists experiments newest first with status (`running`, `completed`, `failed`), dataset and version, samples uploaded out of items × samples, cost (`—` with none uploaded), git branch and commit. The Dataset select filters with `?dataset=<id>`; "Clear" removes it. A dataset page links here with "Experiments on this dataset →"; the project home page (`/p/<slug>`) and the nav with "Experiments".
- `ex-ui-page` `/experiments/<id>` has the header (dataset version link, counts, cost, started and duration, git, entry, hash), the stop reason for a `failed` run, a Scorers table (what each checks, pass rate, passed/scored, mean score, each repeat's rate), and an Items table with passed/scored per scorer (`n/a` when the item lacks the scorer's expectation), errored samples, and mean latency.
- `ex-ui-item` `/experiments/<id>/items/<item_id>` has the input and expectations, then each sample: scores with ✓/✗ and reasons, output (or the error), and expected vs actual tool calls lined up by the first comparing trajectory scorer's mode, with the first mismatch outlined and labelled, missing calls `✗` and extra ones `+`. Each sample links its trace, or says "trace expired" once it's gone (experiment traces aren't pinned).
- `ex-cli` `agenttrace eval -c <eval.yaml>` prints one line per sample and a summary (pass rates with each repeat's rate, items failing most samples, cost), and exits 0 on a completed experiment. Before anything runs, a bad `eval.yaml`, a missing `AGENTTRACE_API_KEY`, an unknown dataset or version, or an import error exits 2 and records nothing.

## How to get to it (user POV)

- The CLI: `uv run agenttrace eval -c examples/demo-agent/eval.yaml` from the repo root, with `AGENTTRACE_API_KEY` and `AGENTTRACE_ENDPOINT` set.
- The web app: the project home page → "Experiments →" (or "Experiments" in the nav), or a dataset page → "Experiments on this dataset →". Then an experiment's name, then an item's input.
- The API directly: `curl -H "Authorization: Bearer $AGENTTRACE_API_KEY" http://127.0.0.1:18000/v1/experiments`.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run. `ANTHROPIC_API_KEY` in `.env` for the CLI journey (12 short demo-agent runs, about $0.20); the API journey needs none.

- **CLI journey.** `python3 -I .claude/skills/verify/bin/seed_support_dataset.py http://127.0.0.1:18000 "$KEY"` publishes `support` v1 (4 items, the last one built to fail). Then, with `AGENTTRACE_API_KEY="$KEY" AGENTTRACE_ENDPOINT=http://127.0.0.1:18000`, `uv run agenttrace eval -c examples/demo-agent/eval.yaml --name "verify run"` prints 12 sample lines and ends with `completed  12 samples over 4 items`, `answer 6/6`, `steps 9/12`, `few-steps 12/12`, and `items failing most samples: … steps 0/3`; exit 0. The failing item's reason in `experiment_scores` is `expected escalate; not called in order`. Each sample's `trace_id` has an `agenttrace.eval.sample` root with `support_agent` under it, `retention_days` 30 (not pinned), and the spans' summed `cost_usd` equals the experiment's `cost_usd`.
- **Experiment pages.** After the CLI journey, `/experiments` lists the run `completed` at `12/12`. Its page shows `answer 6/6`, `steps 9/12 75%`, `few-steps 12/12`, and the Items table has `n/a` under `answer` for the two items without an expected output and a red `0/3` under `steps` for "Where is order 1045?". That item's page outlines Expected `escalate` as "missing · first mismatch" and marks the actual `lookup_order(order_id="1045")` "extra" on every sample; each sample's trace link opens an `agenttrace.eval.sample` trace with the same cost.
- **Other states.** Over the API, create an experiment on a second dataset and finish it `failed` with an error: the list shows `failed`, `0/1`, cost `—`, and its page shows "The run stopped." with the error. Create one on `support` with a `trajectory` scorer in `exact` mode and upload a sample whose calls are `lookup_order, search_kb, issue_refund` against an expected `lookup_order, issue_refund`, leaving it running: the list shows `running` and `2/8`, and the item page outlines actual `search_kb` (#2) as the first mismatch, with expected `issue_refund` missing. Filtering by the second dataset lists only its experiment.
- **Expired sample trace.** `H ch expire-trace "ALTER TABLE traces DELETE WHERE trace_id = '<a sample's trace_id>' SETTINGS mutations_sync = 1"`: that sample shows "trace expired" with no link; the others keep theirs.
- **CLI refusals.** `--dataset-version 9` exits 2 with `404 no version 9`; a copy of eval.yaml with `dataset: nope` exits 2 with `no dataset named "nope"`; `SELECT count(*) FROM experiments` doesn't change.
- **API journey.** A stdlib-only script against `http://127.0.0.1:18000` that publishes a two-item dataset (one `expected_output`, one `expected_trajectory`), creates an experiment with `samples: 2` and an `exact` and an `order` scorer, posts the same create twice more (the second with `samples: 3`), uploads three samples twice, finishes `completed`, uploads the fourth as errored, finishes again, then uploads once more. Expect `201`, `200`, `409 … different settings`, `200`, `200`, `422 3 of 4 samples uploaded…`, `200`, `200`, `409 … completed; its results can't change`. The detail then shows `samples_uploaded: 4`, both scorers at `pass_rate: 0.5` with `pass_rate_by_sample: [1.0, 0.0]`, and one item with `errored: 1`.
- **Cost.** With `usage: [{"model": "claude-sonnet-5-5", "input_tokens": 2000, "output_tokens": 300}]` each sample costs `0.007` ($2 and $10 per MTok in the seeded table), so 4 samples show `cost_usd: 0.028`.
- **Stored rows.** `docker exec agenttrace-verify-postgres-1 psql -U agenttrace -d agenttrace -c "SELECT status, finished_at IS NOT NULL FROM experiments"` and `SELECT count(*) FROM experiment_samples` / `experiment_scores` match the journey (4 samples, 4 scores).
- **Integration tests.** The same env as the datasets entry, then `uv run pytest services/ingest -m integration` covers the journey in Postgres and pinning in ClickHouse.

## Gotchas

- Experiments reference a published version: create and publish the dataset first, and take item IDs from `GET /v1/datasets/<id>/versions/1`, since the draft's IDs are the same items but only versioned ones count.
- An errored sample is uploaded with `error` set and a score of 0 for each scorer that applies; the server doesn't fill in missing scores.
- After editing only the web app, `H web-restart` picks it up without losing the seeded data; `up` again would start from empty volumes.
- The demo agent's answers vary between runs, so `answer` and `steps` can occasionally fail on the good items; only the built-to-fail item's `steps 0/3` is guaranteed. Don't treat one stray failure as a regression.
- Experiment traces aren't pinned (ADR-0021), so `trace_id` links on old experiments may point at expired traces.
