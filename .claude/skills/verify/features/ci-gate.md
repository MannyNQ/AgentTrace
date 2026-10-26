# CI gate

A completed experiment is compared with its baseline (ADR-0007, ADR-0026): the newest completed experiment with the same `eval.yaml` hash at the merge-base commit, else the newest on the base branch, else none. Ingest computes the comparison (ADR-0024): per scorer, the pass rate over items scored in both runs, a paired item-level bootstrap 95% interval of the change, and a regression when the interval is below zero and the drop exceeds the scorer's `max_drop` (ADR-0025). `agenttrace eval` prints it, writes it as the PR comment's markdown, and with `--fail-on-regression` exits 3 on a regression. The web app shows it on the experiment page and at `/experiments/<id>/compare/<baseline>`. The `llm_judge` scorer grades with an Anthropic model, untraced, with its cost kept apart (ADR-0027). `action.yml` runs all this in GitHub Actions.

## Sub-features

- `gate-baseline` a run with `--base <branch>` (or `GITHUB_BASE_REF` in a PR) records `git_merge_base` and `git_base_branch`; the baseline is the run whose `git_commit` is the merge-base (`at the merge-base`), else the newest on the base branch (`newest on the base branch`), else none (`baseline: none yet`, exit 0).
- `gate-api` `GET /v1/experiments/<id>/comparison[?baseline=<id>]`: 409 unless both are `completed`, 409 for a different hash (`the baseline ran a different eval.yaml (its hash differs)`), 404 for an unknown baseline. Returns `scorers` (rates, change, `ci_low`/`ci_high`, `max_drop`, `regressed`, `underpowered`, `items`), `item_regressions`, `added_items`/`removed_items`, and cost/latency stats.
- `gate-cli` `--fail-on-regression` exits 3 on a regression, 0 otherwise (including no baseline); `--report-md <path>` writes the comment: verdict, scorer table, underpowered note, cost and p50 latency, up to 10 item regressions named by item ID (never by input) linking to the side-by-side view (with `--web-url`), items added/removed. A create the server refuses (an older ingest, say) exits 2.
- `gate-ui-panel` the experiment page's "Versus baseline" section: verdict, baseline link with how it was chosen, the scorer table, "Open the comparison (N item regressions) →", and "Compare with…" listing only completed runs with the same hash. A run without a baseline says "No baseline yet".
- `gate-ui-compare` `/experiments/<id>/compare/<baseline>`: the scorer table, items with `baseline → this run` pass counts per scorer, item regressions first and shaded, items added and removed. A refused pair shows the API's reason.
- `gate-ui-item` `/experiments/<id>/compare/<baseline>/items/<item>`: the input, then the baseline's and this run's samples side by side with scores, outputs, and tool calls against the expected ones.
- `gate-judge` `type: llm_judge` with `model` and `criteria` (and optional `include`): a pass/fail verdict with a reason per sample, `judge_usage` and `judge_cost_usd` stored per sample, `judge $…` on the experiment header, and no judge spans in ClickHouse. An unreachable model skips the scorer for that sample (`(skipped helpful: judge unavailable: …)` in the CLI line); a misspelled `model` or missing key exits 2 with `can't use judge model …` before any agent runs.
- `gate-action` `action.yml` and `.github/workflows/eval-gate.yml`: on a pull request, a passing run and a broken run (`EVAL_GATE_BROKEN=1`) each post a sticky comment, and the broken one fails with exit 3.

## How to get to it (user POV)

- The CLI: `uv run agenttrace eval -c examples/demo-agent/eval.yaml --base main --fail-on-regression --report-md report.md --web-url http://127.0.0.1:13000`.
- The web app: an experiment's page → "Versus baseline" → "Open the comparison" or "Compare with…" → an item's input.
- The PR comment's item links, which open the side-by-side view.
- The API: `curl -H "Authorization: Bearer $AGENTTRACE_API_KEY" http://127.0.0.1:18000/v1/experiments/<id>/comparison`.
- GitHub Actions: `uses: MannyNQ/AgentTrace@<ref>` with `api-url` and `api-key`; this repo's own `Eval gate` workflow runs it on PRs that touch the SDK, ingest, or the Action.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run, `support` seeded with `seed_support_dataset.py` (see the experiments entry), `git fetch origin main`, and `MB=$(git merge-base HEAD origin/main)`. `ANTHROPIC_API_KEY` in `.env` for the demo-agent and judge journeys (about $0.40 for two 12-sample runs plus a 4-sample judge run); the scripted journey needs none. Below, `ENV` is `AGENTTRACE_API_KEY="$KEY" AGENTTRACE_ENDPOINT=http://127.0.0.1:18000`.

- **Baseline on main.** `GITHUB_EVENT_NAME=push GITHUB_SHA=$MB GITHUB_REF_NAME=main ENV uv run agenttrace eval -c examples/demo-agent/eval.yaml --name "main baseline"`: exit 0, `steps 9/12`, and `baseline: none yet`.
- **PR passes.** `ENV uv run agenttrace eval -c examples/demo-agent/eval.yaml --name "pr good" --base main --fail-on-regression --report-md $RUN/pr-good.md --web-url http://127.0.0.1:13000`: exit 0, `vs baseline <main baseline id> (at the merge-base): no regressions`; the markdown starts `### ✅ AgentTrace eval: no regressions` and names the merge-base commit.
- **PR regresses.** The same with `ANTHROPIC_API_KEY=invalid` and `--name "pr broken"` (every sample errors): exit 3, `answer`, `steps`, and `few-steps` regressed (`-75 pts (-100 pts to -25 pts)` for steps), the underpowered note, and 9 item regressions (`item <id>`) linking to `/experiments/<pr broken>/compare/<main baseline>/items/<item>`.
- **UI.** The "pr broken" page shows "3 regressions vs main baseline (at the merge-base · <sha>)" and "Open the comparison (9 item regressions) →". The compare page lists every item with red `3/3 → 0/3` cells shaded; "Where is order 1045?" shows `0/3 → 0/3` under steps (it failed in both). The item view shows the baseline's passing samples beside the errored ones, with `lookup_order` marked "missing · first mismatch". "Compare with…" lists "pr good" and "main baseline" only.
- **Judge.** A copy of eval.yaml with `samples: 1` and `{name: helpful, type: llm_judge, model: claude-haiku-5-5, criteria: …, include: [input, output, trajectory]}`: each line shows `helpful:pass` or `helpful:FAIL`, `baseline: none yet` (new hash), and `SELECT c.reason, s.judge_usage, s.judge_cost_usd …` has a reason, a `claude-haiku-5-5` usage entry and a cost per sample. `H ch x "SELECT count() FROM spans WHERE parent_span_id = '' AND name = 'anthropic.messages.create'"` is 0, and sample traces hold only the agent's model. Its page shows `judge $…`, "No baseline yet", and `llm judge · claude-haiku-5-5 · <criteria>`; `/experiments/<judge run>/compare/<main baseline>` shows "the baseline ran a different eval.yaml (its hash differs)".
- **Scripted gate (what the Eval gate workflow does).** With `.github/eval-gate/`: a baseline as above with `-c .github/eval-gate/eval.yaml`, then `--base main --fail-on-regression` exits 0, and `EVAL_GATE_BROKEN=1 … -c .github/eval-gate/eval-broken.yaml` exits 3 with `answer` and `steps` regressed. No model calls.
- **Integration tests.** The datasets entry's env, then `uv run pytest services/ingest -m integration`: includes a PR run compared with its merge-base run in Postgres and the base-branch fallback. `SELECT name FROM schema_migrations` lists `0003_comparisons.sql`.
- **The Action.** Only GitHub runs it: open or push a PR that touches `sdk/python/**`, `services/ingest/**`, or `action.yml`, and check the `Eval gate` job passes with two comments on the PR (`✅ … no regressions` and `❌ … 2 regressions`), each updated in place on the next push.

## Gotchas

- The baseline must have the same `eval.yaml` hash: a copy with any scorer changed (the judge run) never compares with the demo runs. `max_drop`, `name`, `concurrency`, and `version` don't count.
- `--base` computes the merge-base of the run's commit with `origin/<base>`; fetch first, or it falls back to the newest run on the base branch and says so on stderr.
- The demo agent's answers vary, so a real "pr good" run can drift by a sample; with 4 items the comparison is always underpowered, and only a large drop (the broken run) regresses.
- The judge's reasons are model output and differ between runs; check that verdicts and costs are present, not their wording. It sees tool arguments, not tool results.
- After `H web-restart` the browser pane may refuse the first navigation; open the URL again with `preview_start`.
