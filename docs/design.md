# AgentTrace design doc (v1)

AgentTrace is a self-hostable, open-source platform for teams building LLM agents. It answers three questions: what did my agent do (tracing), why did it fail and does my fix work (replay), and did my change break anything else (evals in CI).

Vocabulary is defined in [`GLOSSARY.md`](../GLOSSARY.md). Lasting decisions are in [`docs/adr/`](adr/). This doc ties them together.

## Angle

Failures become tests, and tests grade trajectories (ADR-0001). A failed production trace becomes a dataset item in one click; the CI eval then checks which tools the agent called, in what order, not just its final answer.

## Architecture

```
 agent + SDK ──spans (batched, async)──▶ ingest API ──▶ Redis Streams ──▶ workers
                                                                            │
          ┌─────────────────────────────────────────────────────────────────┤
          ▼                          ▼                                      ▼
   Postgres                    ClickHouse                         S3 (SeaweedFS locally)
   orgs, projects,             spans: append-only,                payloads > 8 KB
   datasets, experiments       queried in aggregate

 web (Next.js) ──▶ query API ──▶ Postgres + ClickHouse + S3
 agenttrace CLI (eval runner, runs on the client) ──▶ results upload
```

| Store | Holds | Why |
|---|---|---|
| Postgres | Orgs, projects, datasets, experiments | Relational data that changes and needs transactions |
| ClickHouse | Spans | Append-only, high volume, read as aggregates (latency percentiles, cost by model) |
| Redis Streams | Spans in flight | Ingest returns immediately; a database outage doesn't lose spans |
| S3 | Payloads over 8 KB | Keeps ClickHouse rows small so analytics stay fast (ADR-0009) |

Key constraints from the ADRs:

- Every row is scoped by `project_id` from day one; auth arrived in Phase 6 (ADR-0002, ADR-0031 to ADR-0034).
- The SDK generates OTel-format IDs, so `(trace_id, span_id)` is the dedup key (ADR-0003).
- A trace is complete when its root span ends or after a 5-minute quiet period (ADR-0004).
- The eval runner runs on the client; the server never executes user code (ADR-0006).
- Cost is computed once at ingestion (ADR-0010).

## Repo layout

| Path | What |
|---|---|
| `sdk/python/` | `agenttrace` package. Phase 0: the span contract (`Span`, `SpanKind`, ID helpers) |
| `services/ingest/` | FastAPI ingest API. Phase 0: `/health` across all four stores |
| `services/worker/` | Redis Streams consumer. Phase 0: joins the `workers` group and acks |
| `web/` | Next.js UI. Phase 0: a service health page |
| `examples/demo-agent/` | Customer-support agent with 5 mock tools; the test fixture for every later phase |
| `infra/` | ClickHouse and Postgres init SQL, SeaweedFS S3 config |

## Decisions settled in Phase 1

1. **Sort key and trace lookup.** `spans` keeps `(project_id, start_time, trace_id, span_id)`. A materialized view fills a `traces` summary table; trace completion and status are computed at query time (ADR-0013).
2. **Dedup.** The worker skips `(trace_id, span_id)` pairs already marked in Redis; single-trace reads use `FINAL` (ADR-0014).
3. **Redis Streams.** At-least-once delivery, `XAUTOCLAIM` after 60 s idle, a `spans:dead` stream after 5 deliveries, `MAXLEN ~ 1000000` (ADR-0015).
4. **Per-project retention.** A `retention_days` column stamped at ingestion drives the TTL (ADR-0016). Pinning is settled in Phase 3.
5. **Ingest wire format.** JSON only, whole-batch validation, API keys resolve the project (ADR-0017). OTLP comes later.

Also settled: the web app reads ClickHouse and S3 directly until Phase 6 (ADR-0018); spans carry separate `input_uri` and `output_uri`; prices live in a Postgres `model_prices` table; the SDK sends each span when it ends and does nothing without an API key.

## Phase 1 plan

The demo agent's runs appear in the UI:

1. SDK: `@trace` decorator, `span()` context manager, `contextvars` propagation, background batching exporter, Anthropic client auto-instrumentation.
2. Ingest: `POST /v1/spans` (JSON), API-key auth stub resolving to the default project, push to Redis.
3. Worker: validate, offload payloads, compute cost, batch insert into ClickHouse.
4. Web: trace list and a basic trace tree.

## Decisions settled in Phase 2

1. **Dataset writes go through the ingest API.** `/v1/datasets` authenticates with API keys like `/v1/spans`. The web app calls it from server actions; the Phase 3 CLI will use the same endpoints (ADR-0019).
2. **Versions are lists of item revisions.** Editing a draft item adds a revision; a version points at one revision per item, so unchanged items are shared between versions. Publishing requires an expectation on every new or changed item (ADR-0020).
3. **Optimistic concurrency.** Every item edit and delete names the revision it was based on. A stale revision gets 409, and the UI says to reload.
4. **Migrations.** Ingest applies numbered SQL files from `agenttrace_ingest/migrations/` on startup, recording each in `schema_migrations` under an advisory lock. Existing stacks upgrade in place; the Phase 0 and Phase 1 init SQL stays as it is.
5. **Add to dataset copies the input.** The server reads the trace's root span from ClickHouse, fetches the full input from S3 when it was offloaded, and copies it into Postgres. The client sends only the trace ID. An input the SDK truncated can't be added.

Also settled:

- Dataset names are slugs (`[a-z0-9][a-z0-9_-]*`, up to 64 characters), unique per project, archived datasets included; a taken name's error points at the dataset holding it. Datasets are addressed by a stable `id`, so they can be renamed. They're archived, never deleted; archived datasets are hidden from the list (until "Show archived") and from the "Add to dataset" picker.
- `POST /v1/datasets` takes an optional `trace_id`, so "Add to dataset" with a new dataset is one request: a trace that can't be copied creates nothing.
- Item contents must be storable in `jsonb`: no NaN or Infinity, and no NUL characters. Those are refused with 422, from the API and from "Add to dataset".
- `expected_trajectory` is a list of `{"tool": "<name>", "args": {...}}` with `args` optional. `args` is a partial match: listed keys must be equal, other keys are ignored. Regex and wildcard matching are left to Phase 3 scorers. `metadata` is a string-to-string map.
- `input` and `expected_output` are each capped at 1 MB of JSON.
- No CSV or JSONL import.
- No pinning yet (ADR-0011, Phase 3). When a linked trace expires, the item keeps its copy of the input and the UI shows "source trace expired".

## Phase 2 plan

A failed trace becomes a dataset item, and a dataset can be published as a version for Phase 3's eval runner:

1. Ingest: migration runner, dataset schema, and the `/v1/datasets` API with its rules.
2. Web: dataset list, dataset page (draft and versions), JSON item editor, publish, version view with what was added, changed, and removed, and "Add to dataset" on the trace page.
3. Proof: API unit tests with a fake store, integration tests against Postgres, ClickHouse and S3, and a `datasets` entry in the verify skill.

## Decisions settled in Phase 3

1. **Pinning rewrites retention in place.** Giving an item a `source_trace_id` rewrites that trace's `retention_days` with an `ALTER TABLE ... UPDATE` on `spans` and `traces` so it expires on 2106-01-01; items record `pinned_at`, and ingest pins any unmarked item's trace on startup, which backfills Phase 2 items and retries failed pins. Experiments don't pin (ADR-0021, amending ADR-0011).
2. **Entry point and trajectory capture.** `eval.yaml` names `module:function`, called with the item's `input`; sync and async are both accepted. Each sample runs under a root span the runner opens, and the runner collects its tool spans through a new SDK span-processor hook, with no server round trip (ADR-0022).
3. **Experiments stream through the ingest API.** `/v1/experiments` with client-generated IDs: created `running`, samples upserted as they're scored, finished `completed` or `failed`. The server computes sample cost on arrival (ADR-0023).

Also settled:

- `eval.yaml` names a dataset and a `version`, either a number or `latest` (the default), resolved to a number when the run starts.
- A scorer is skipped for an item that lacks the expectation it needs (no `expected_output` for an output scorer, no `expected_trajectory` for `exact`, `in_order`, or `any_order`). Pass rates show their denominator.
- Trajectory modes `exact`, `in_order`, and `any_order` compare against the item's expected trajectory; `forbidden` (a tool list) and `max_steps` (a limit) take their parameters from the scorer config and apply to every item. One scorer entry has one mode; list several for several. Scores are 1 or 0, with a reason naming the first mismatch. Arguments match by Phase 2's partial equality; regex and wildcards wait for a dataset that needs them.
- Built-in scorers in Phase 3: exact match, contains, regex, JSON schema, and trajectory, plus custom Python scorers by import path. The LLM judge moves to Phase 4.
- An errored sample (the agent raised or exceeded `timeout_s`, default 120) scores 0 on every scorer.
- `agenttrace eval` ships in the `agenttrace` package (`agenttrace.eval`); PyYAML and jsonschema sit behind the `agenttrace[eval]` extra. It exits 0 when the experiment completes, 1 when it's `failed`, and 2 when the run couldn't start, in which case nothing is recorded.
- Each scorer type in `eval.yaml` has its own fields, and unknown fields are errors. `exact_match` compares JSON values (strings without surrounding whitespace); `contains` checks each of a list of phrases, case-insensitively by default; `regex` takes a `pattern` for every item, or the item's `expected_output` as the pattern; `json_schema` reads `schema` or `schema_file` (relative to `eval.yaml`, read at load so it's hashed); `custom` names a function taking a `Case` and returning a bool, a 0-1 number, `(score, reason)`, or None to skip.
- The `eval.yaml` hash leaves out `name`, `concurrency`, and `version`; `--dataset-version` overrides the version without changing it.
- Samples run on one asyncio loop: async entry points directly, sync ones on a thread pool, both bounded by `concurrency`, with the timeout counted from each sample's start. A timed-out async sample is cancelled; a sync one can't be, so its thread is abandoned and the CLI exits without waiting for it. A run that stops (an upload refused, Ctrl-C) is marked `failed` at once and the CLI exits without waiting for samples still running.
- Experiments record git commit, branch, scorer configs, and the `eval.yaml` hash now, though only Phase 4 reads them.

## Phase 3 plan

`agenttrace eval` runs the demo agent over a published dataset version, and the experiment shows up in the UI with per-item trajectory scores:

1. SDK: span-processor hook, `eval.yaml` loading and hashing, the runner (thread pool and async, per-sample root spans, timeouts), the built-in scorers and custom scorer loading, and the results uploader.
2. Ingest: experiments schema and `/v1/experiments` (create, upsert samples, finish, list, read), cost from the price table on sample upload, pinning on item creation, and the backfill migration.
3. Web: experiments list (filterable by dataset) and an experiment page with per-scorer pass rates (mean and spread across samples, with denominators), a per-item table of pass counts, and per-sample output, actual versus expected trajectory with the first mismatch marked, scorer reasons, and the trace link.
4. Demo agent: an `eval_entry` adapter and an `eval.yaml`.
5. Proof: scorer unit tests, runner tests with a scripted fake agent and a fake API, experiments API integration tests against Postgres, and an `evals` entry in the verify skill that seeds and publishes a dataset with one item built to fail its trajectory check, runs `agenttrace eval`, and checks the experiment in the UI.

## Decisions settled in Phase 4

1. **Comparisons are computed in ingest.** `GET /v1/experiments/{id}/comparison` selects the baseline (or takes `?baseline=`), runs the bootstrap with a fixed seed, and lists item regressions. Both experiments must be `completed` with the same `eval.yaml` hash, or it's 409. The CLI and the web app both call it, so the PR comment and the UI can't disagree (ADR-0024, amending ADR-0018).
2. **Paired item-level bootstrap and `max_drop`.** One metric per scorer: its pass rate over the items scored in both runs. The bootstrap resamples items, not samples. A scorer regresses when the 95% interval lies below zero and the drop exceeds its `max_drop` (default 0.05, outside the hash). Underpowered means the interval's half-width exceeds `max_drop` (ADR-0025, amending ADR-0008).
3. **The CLI records head, merge-base, and base branch.** On a PR, `git_commit` is the PR head SHA from the event payload, not `GITHUB_SHA`; the CLI computes `git_merge_base` itself and records `git_base_branch`, and the server picks the baseline from those (ADR-0026, refining ADR-0007).
4. **The LLM judge runs client-side.** `llm_judge` on Anthropic with required `model` and `criteria`, a binary verdict as structured JSON output (current models refuse forced tool use), skipped (not 0) after 3 failed retries, untraced, with `judge_cost_usd` stored per sample (ADR-0027).

Also settled:

- Cost per sample and p50 latency are compared but don't gate.
- Item regressions are per scorer: more than half the item's samples passed in the baseline and more than half fail now. Listed, never gating.
- `agenttrace eval --fail-on-regression` exits 3 on a metric regression; 0, 1, and 2 keep their Phase 3 meanings, and with the flag a comparison that can't be fetched exits 1 (fail closed). No baseline means exit 0. Without the flag, `eval` still prints the comparison. No separate `compare` command. `--base <branch>` finds a baseline outside a GitHub pull request, for local previews.
- `--report-md <path>` writes the comparison as the PR comment's markdown, so rendering is tested Python and can be previewed locally.
- The GitHub Action is a composite `action.yml` at the repo root (`MannyNQ/AgentTrace@v0`). Inputs: `config`, `api-url`, `api-key`, `web-url`, `github-token`, plus `run-prefix` (e.g. `uv run`, for agents whose dependencies live in a uv project) and `install` (pip-install `agenttrace[eval]` from the action's copy of the SDK, default on). On a pull request it unshallows the checkout and fetches the base branch for the merge-base, runs `eval` with `--fail-on-regression` only on `pull_request`, writes the markdown to `$GITHUB_STEP_SUMMARY`, and creates or updates one sticky comment per config, found by `<!-- agenttrace:<eval.yaml path> -->` and by its author (the token's user). On pushes it runs ungated, which builds the base branch's baselines.
- On fork PRs, secrets are unavailable: the Action skips with a step-summary note and exits 0. No `pull_request_target`.
- The PR comment, top to bottom: verdict (✅ / ❌ N regressions / ⚪ no baseline) with links to both experiments and how the baseline was chosen; a per-scorer table (baseline, current, change with 95% CI, `max_drop`, denominator, regressed / ok / underpowered); cost per sample and p50 latency; up to 10 item regressions (by item ID) linking to their compare view, then "+N more"; counts of items added and removed.
- The judge is configured with `model`, `criteria`, and optional `include` (`input`, `output`, `expected_output`, `trajectory`); all of it is in the hash. Judge calls share `concurrency`. A misconfigured judge (no credentials, a bad key, an unknown model) exits 2 before the run, or stops it, rather than skipping every sample.
- Scoring runs on threads so judge calls overlap, but custom scorers are still called one at a time, as in Phase 3.
- The PR comment and job summary name items by ID, never by input: items are often copied from production traces, and a comment can be public.
- The CLI sends only fields that differ from their defaults, so a newer SDK or Action still works against an older ingest until it uses a newer feature. A refused create exits 2.
- Ingest keeps the last 256 comparisons in memory by (experiment, baseline): both are completed, so they can't change, and the bootstrap costs about 1.5 s per scorer at 1,000 items.

## Phase 4 plan

A pull request's eval is compared with its baseline, posted as a PR comment, and fails the check on a real regression:

1. Comparison core: an experiments migration (`git_merge_base`, `git_base_branch`, judge token counts and `judge_cost_usd`), baseline selection, the paired bootstrap, item regressions, and the comparison endpoint.
2. CLI gate: head SHA, merge-base and base branch capture, `max_drop` in `eval.yaml`, the comparison printout, `--fail-on-regression` with exit 3, and `--report-md`.
3. GitHub Action and PR comment: `action.yml`, the sticky comment, the step summary, the fork skip, and a README for the Action.
4. Compare UI: a "vs baseline" panel on the experiment page with a "Compare with…" picker limited to completed experiments with the same hash, and `/experiments/[id]/compare/[baselineId]` with the scorer table, items sorted regressions first, and per-item baseline and current samples side by side.
5. LLM judge: the `llm_judge` scorer, its retries and skip, suppressed tracing, and judge cost. It goes last and can slip without blocking the gate.
6. Proof: unit tests for the bootstrap, baseline selection, and item regressions; CLI tests for exit 3 and the markdown; a `ci-gate` entry in the verify skill that runs a baseline, breaks the demo agent, and checks for exit 3, the compare view, and the rendered comment; and an `Eval gate` workflow that runs `action.yml` for real on pull requests touching the SDK, ingest, or the Action, against a stack started in the job, with a scripted agent (`.github/eval-gate/`) once passing and once broken. This repo's CI doesn't gate on the demo agent, since a fresh stack has no baseline history and the demo agent needs a model key.

## Decisions settled in Phase 5

1. **A replay re-runs the whole trace on the client.** `agenttrace replay <trace_id>` calls the user's entry point with the trace's root input. Each run's trace carries `replay_of`, the original's root span `(trace_id, span_id)`, so fork-from-step can come later without a schema change. A server-side single-call playground is out under ADR-0006 (ADR-0028, building on ADR-0022).
2. **Replays are their own entity.** A Postgres `replays` table behind `/v1/replays` holds the group, its status, and per-run scores. The lifecycle is experiments' (ADR-0023): client-generated IDs, `running`, runs upserted as scored, then `completed` or `failed`. ClickHouse's trace summary gets a `replay_of` column. Replay traces expire normally and replaying doesn't pin the original (ADR-0029, consistent with ADR-0021).
3. **Replays score against the item's current draft revision.** If the trace is the `source_trace_id` of an item in `eval.yaml`'s dataset draft and the item has an expectation, the runs are scored with `eval.yaml`'s scorers and the revision `(item_id, rev)` is recorded. Otherwise the replay is unscored (ADR-0030).

Also settled:

- Fork-from-step waits for fuller LLM capture: today LLM spans record only `system` and `messages` as input, parameters as strings, and tools by name without schemas. Tools run live, as in `eval`; stubbing recorded tool results waits for the same ADR.
- The entry point comes from `eval.yaml` (`agenttrace replay <trace_id> [--config eval.yaml]`); `--entry module:function` overrides it. `--entry` with no `eval.yaml` is allowed and unscored, with `samples` 3, `timeout_s` 120, and `concurrency` 1.
- `GET /v1/traces/{trace_id}/input` returns the root span's input with Add to dataset's copy logic, fetching it from S3 when offloaded. A truncated input is refused with 422. The CLI never touches the stores.
- `--samples k` defaults to 3, as in `eval`. Each run is its own trace with `replay_of`. Runs use the eval runner unchanged: `concurrency` and `timeout_s` from `eval.yaml`, a runner-opened root span per run, and an errored run scores 0.
- Any trace whose root span input is available untruncated can be replayed, including experiment sample traces and traces still running. Replaying a replay resolves to the original's root span, so `replay_of` never chains.
- A trace is in a dataset's draft at most once, so the lookup finds at most one item.
- A replay records the git commit, branch, a dirty flag for uncommitted changes, the `eval.yaml` hash, and scorer configs. The UI shows e.g. "at abc123 + uncommitted changes".
- The first difference compares trajectories position by position. A step differs on tool name or on `args` by full JSON equality, not the partial match used for expected trajectories, because the original is a recording, not an expectation. The first differing index is marked; a trajectory that is a prefix of the other differs at the first missing step. Outputs are shown side by side with no computed text diff.
- The replay page always shows each replay run's trajectory and output beside the original's, with the first difference marked. The CLI prints pass counts when the replay is scored, and a link to the replay page. The loop is: Add to dataset, set an expectation, replay until it passes, publish, CI gate.
- Exit codes: 0 when the replay completes, 1 when it's `failed`, 2 when it couldn't start. `--require-pass` exits 3 when the scored runs don't all pass, or when no run was graded because every scorer skipped. With nothing to score (no linked draft item, or no expectation), `--require-pass` exits 2 before running and says why, failing closed like `eval`'s comparison.
- The trace list hides replay traces unless "Show replays" is on.
- The trace page gets a "Replays" panel (time, commit and dirty flag, pass counts if scored) and a "Replay" button that shows the CLI command to copy, since the server can't run it (ADR-0006). A replay page shows the original and the k runs side by side with the Phase 4 side-by-side component, the first difference marked, and scorer reasons. There is no top-level replays list.
- The server computes each run's cost on arrival, and the CLI reuses the byte-bounded uploader.
- ClickHouse schema changes are migrations too. Ingest applies numbered SQL files from `agenttrace_ingest/migrations/clickhouse/` on startup and records each in a ClickHouse `schema_migrations` table, as it does for Postgres. The init SQL stays as it is, so existing stacks upgrade in place. Phase 5's file adds `replay_of` to `traces` and swaps `traces_mv`'s query in place with `MODIFY QUERY`, so no insert goes unsummarized; traces already stored get `''`, which is right, since none of them are replays. Migrations retry in the background while ClickHouse is down, so ingest still takes spans.
- Phase 6 is still auth.

## Phase 5 plan

A failed trace is re-run against a fix, and the result is compared with the original and scored against the item's expectation:

1. Ingest: a ClickHouse migration runner, a replays migration (the `replays` table and its runs, and a `replay_of` column on `traces` filled from the root span by `traces_mv`), `/v1/replays` (create, upsert runs, finish, list, read) with cost on arrival, and `GET /v1/traces/{trace_id}/input`.
2. SDK and CLI: `agenttrace replay` with `--config`, `--entry`, `--samples`, and `--require-pass`; `replay_of` on each run's root span; root resolution for replays of replays; the draft-item lookup and scoring; git and dirty-flag capture; the pass-count printout; exit codes 0, 1, 2, and 3.
3. Web: the "Replays" panel and "Replay" button on the trace page, the "Show replays" toggle on the trace list, and the replay page with the original and the runs side by side, the first difference marked, and scorer reasons.
4. Proof: unit tests for the first difference and replay scoring; CLI tests with a scripted fake agent for exits 0, 2, and 3 and for chain flattening; integration tests for `/v1/replays` and `/v1/traces/{id}/input`, with offloaded and truncated input; and a `replay` entry in the verify skill that breaks the demo agent, adds the trace to a dataset, sets an expectation, replays (fails), fixes the agent, replays (3/3), and checks the replay page. No new CI workflow.

## Decisions settled in Phase 6

1. **Ingest is the only reader of the stores.** Trace, span, and payload reads move behind `/v1/traces`; the web container keeps only `INGEST_URL` (ADR-0031, superseding ADR-0018).
2. **Ingest owns users and sessions.** Local email and password (argon2id), opaque hashed session tokens in Postgres, the web app forwarding the token from an httpOnly cookie. Session requests name the project in `AgentTrace-Project: <slug>` on the same `/v1` paths. Users belong to one org as `owner` or `member` (ADR-0032).
3. **API keys have a scope.** `ingest` (spans only) or `full`. The `.env` key stays a `full` key for the default project (ADR-0033).
4. **Claiming an install takes a setup token**, from `AGENTTRACE_SETUP_TOKEN` or printed in ingest's logs (ADR-0034).

Also settled:

- Several projects per org, with the project in the URL: `/p/<slug>/traces`. A slug is generated from the name at creation, is unique within the org, and never changes; the name stays editable. The default project's slug is `default`.
- Old paths (`/traces/<id>`, `/experiments/<id>`, `/replays/<id>`, ...) redirect to `/p/<slug>/...` for the entity's project, after the same access check as any read, so links already in PR comments keep working. `/` redirects to the last project used, kept in a cookie, else `default`.
- Owners only: members, invites, roles, API keys, creating and renaming projects, and project settings such as `retention_days`. Members can do everything else in every project of the org. Removing a member deactivates them and revokes their sessions; keys they created keep working. The last owner can't be demoted or removed.
- `AGENTTRACE_PUBLIC_URL` is where people open the web app, defaulting to `http://localhost:3000`. The cookie is `Secure` when it's https, and ingest accepts the published dev key only while it's localhost (ADR-0033).
- Password hashing (argon2id, 64 MiB each) runs on at most 4 threads of its own; requests waiting for one wait as coroutines, so a login flood can't exhaust memory or hold up span ingest, whose guard answers cached keys without a thread.
- Sessions last 30 days, sliding, with the expiry extended at most once a day. The cookie is httpOnly, `SameSite=Lax`, and `Secure` when `AGENTTRACE_PUBLIC_URL` is https. Logout revokes the session; a password change revokes the user's other sessions. Ten failed logins per email in 15 minutes get 429, counted in Redis. Passwords need 12 or more characters, with no composition rules. Next server actions check the origin, and the browser never talks to ingest, so there is no separate CSRF token.
- Invites and password resets are owner-created links: single use, 7-day expiry, the role chosen in the invite. `agenttrace-admin reset-password <email>` covers a locked-out owner.
- The SDK, `agenttrace eval`, `agenttrace replay`, and the GitHub Action keep using `AGENTTRACE_API_KEY`. The SDK needs an `ingest` key; eval and replay need a `full` key. There is no `agenttrace login`. The CLI prints links with the project slug.
- Deferred, not to be built in Phase 6: OIDC/SSO, SMTP, per-project access lists, a multi-org UI, an audit log, OTLP ingest, and `agenttrace login`.

## Phase 6 plan

Every request is authenticated, every read and write is checked against the project, and the web app holds no store credentials:

1. Ingest auth core: Postgres migrations (users, memberships, sessions, invites, key scope and fields, project slugs); the auth resolver for keys and sessions with the project header; login, setup, invite, and reset endpoints, with login-failure counters in Redis; the `agenttrace-admin` command; and a test that walks the route table and fails on any `/v1` route without the auth dependency, outside an allowlist (`/health`, login, setup, invite acceptance).
2. Ingest read API: `/v1/traces` for the list, summaries, spans, and payloads, taking over the queries in `web/src/lib/traces.ts`; `/v1/me` and `/v1/projects`.
3. Web: setup, login, and invite pages; `/p/<slug>/` routing and the old-path redirects; all reads through ingest; settings pages for members, keys, and projects; store credentials and the API key removed from the web service in compose. The shared key stays a valid `full` key until this step removes it, so the web app works throughout steps 1 and 2.
4. SDK and CLI: errors that name the missing key scope, and links printed with the slug.
5. Proof and docs: unit tests for the resolver matrix (key scope, session role, project header match or mismatch, revoked or expired); integration tests with two projects, calling every read and write route with the other project's key and session and expecting 403 or 404 with nothing leaked; an `auth` entry in the verify skill that claims a fresh stack with the setup token, invites a member, creates project 2 with an `ingest` key, sends demo-agent spans there and checks project 1 can't see them, checks the `ingest` key gets 403 on `/v1/datasets`, the member can't open the keys page, an old `/replays/<id>` link redirects, and the web container has no store credentials; and the README's "no authentication" warning replaced with a note to serve it behind https.
