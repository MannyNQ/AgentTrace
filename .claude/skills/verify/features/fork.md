# Fork

A fork replays a recorded trace up to a chosen step and runs the user's current code live from there (ADR-0040). Every LLM and tool call before the fork point gets its recorded result instead of running, so the history is fixed and only the decision at the fork point is retested. This is the end-to-end proof of Phase 9: a real failure, a fix, and the failing step retested without repeating any earlier tool call. The pieces (`/steps`, `--from`, `--stub-tools`, the replay page) are in [replay.md](./replay.md).

## Sub-features

- `fork-fixes-the-step` a demo-agent failure recorded with a broken prompt (`lookup_order`, then `issue_refund` for order 1044, 87 days after delivery, then usually `escalate`), forked with a fixed prompt at the LLM step that asked for the refund: every run plays `lookup_order` back first, and none calls `issue_refund` at the fork point.
- `fork-no-prefix-side-effects` the played-back `lookup_order` never really runs: the tool log, which each real tool call appends to, stays empty for the fork, while a whole replay of the same trace with the same log records `lookup_order` (the control).
- `fork-warns-on-the-fix` the fixed system prompt makes step 0's request differ from the recording; each run records the warning `{step: 0, fields: [system, …]}` and still uses the recorded reply, and `diverged_at` stays null.

## How to get to it (user POV)

- Find the failing step on the trace page: select the LLM span before the wrong tool call and open "Fork from here".
- Run the command it shows with the fix in the working tree: `trayce replay <trace> --from <span>`.
- Read the fork's replay page from the replays list on the trace page.

## Driving it with trayce-verify

Preconditions:

- A healthy run (`H doctor`), and `ANTHROPIC_API_KEY` in `.env` (check without printing it: `grep -q '^ANTHROPIC_API_KEY=.\+' .env`). Without it, report every sub-feature as not verified.
- About eight Claude calls.

Run, from the repo root:

```bash
KEY=$(grep '^TRAYCE_API_KEY=' .env | cut -d= -f2-); KEY=${KEY:-tr-local-dev-key}
TRAYCE_API_KEY=$KEY TRAYCE_ENDPOINT=http://127.0.0.1:18000 \
  uv run python .claude/skills/verify/bin/fork_demo.py verify-artifacts/<run-id> | tee verify-artifacts/<run-id>/fork-demo.out
```

`fork_demo.py` drives the real CLI. It records the original with `bin/fork_entry.py` (the demo agent with `FORK_PROMPT=broken`), finds the LLM step that asked for `issue_refund`, runs `trayce replay <trace> --entry fork_entry:entry --from <that span> --samples 2` with `FORK_PROMPT=fixed` and `FORK_TOOL_LOG`, then runs a whole replay as the control. It prints one `ok`/`FAIL` line per check and ends with `FORK OK` or `FORK FAIL` (exit 1). Artifacts in the run dir: `fork-original-<n>.json` (the original's steps), `fork-replay.out` (the CLI's command, exit code and output), `fork-replay.json` (the replay as ingest stores it), the two tool logs, and `fork-control.out` (the control replay's output).

For the UI, sign in and open the original's trace page and the fork's replay page (the replay ID is in `fork-replay.out`): the claims are under `rp-ui-fork` in [replay.md](./replay.md).

## Gotchas

- The model must see `lookup_order`'s result before deciding on the refund, or there's no prefix to play back. A question asking for two things at once ("look up 1042, then refund 1044") gets both tool calls in parallel from the first LLM call, and the wrong decision is step 0. The recipe's question depends on the lookup ("Has order 1044 been delivered? If it has, refund it."), and the helper still retries the recording up to 3 times.
- The broken prompt doesn't make the refund succeed: `issue_refund` checks the window itself and refuses. The failure being fixed is the agent trying, which is what a trajectory scorer with `forbidden: [issue_refund]` would catch.
- The fixed prompt is still a model's judgment, so a run can call `issue_refund` anyway. That's a `FAIL` line for that run, reported as it is; don't loosen the check.
- `fork_entry.py` is verification scaffolding: it patches the demo agent's system prompt and wraps its tools in-process. The demo agent itself never reads `FORK_*`.
