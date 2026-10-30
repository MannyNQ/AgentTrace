Captured 2026-10-30 from a fresh Trayce 0.1.1 stack: `pip install trayce==0.1.1` in a new venv, then `trayce up` (ports moved to 18000 and 13000). The agent and the CLI ran from a checkout of main at bd0e24a, the v0.1.1 commit. Nothing here is edited; links in the outputs point at the local stack that produced them.

The page's proof (support v1, 15 items):

- support-dataset.json: the 15 items, created through the API and published as v1. Items 1-4 are the original items; 5-15 were added on 2026-10-27 so the gate has the power to judge a drop.
- eval-baseline.txt: `trayce eval -c examples/demo-agent/eval.yaml` on main at bd0e24a (experiment 862c4fd1).
- eval-pr.txt, pr-comment.md: the same with prompt-change.diff committed on a local branch, prompt-change @ f15af57, `--base main --fail-on-regression --report-md` (experiment 89e8f2b8). Exit 3: steps regressed 93% → 33%.
- trace-d5d3dd53….jsonl (main) and trace-6696a708….jsonl (PR): sample 1 of item a3bce05f, "Can I get a refund on order 1044?". The hero diagram draws these. Sample 1 is the pair whose main run makes both tool calls from one LLM call, the shape the diagram draws.
- add-to-dataset.json: the PR's failed trace 6696a708 added to the dataset ("Add to dataset", `POST /v1/datasets/<id>/items/from-trace`), then given the expected trajectory lookup_order(order_id="1044") as rev 2.
- replay-1044.txt: `trayce replay 6696a708… -c examples/demo-agent/eval.yaml --require-pass` on main, the prompt fixed. Exit 0, 3/3 runs pass (replay 0d91dbb6).
- demo-agent-1042.txt and trace-08d9c37d….jsonl: `uv run demo-agent "Where is order 1042?"`, the span tree in "What your agent actually did".
- ../img/compare-1044.png, compare-1044-pr.png, replay-1044.png: the web UI in headless Chrome at 2x, light theme. For the compare shots, samples 0 and 2 were hidden so sample 1's pair lines up; nothing else on the page was changed.

v1/ holds the first proof, from 2026-10-27, on the 4-item dataset: the same prompt edit dropped steps 75% → 25%, but with 4 items the gate only flagged it underpowered (eval-pr.txt, pr-comment.md); a softer edit changed nothing (eval-pr-quiet.txt). Those captures predate the rename from AgentTrace to Trayce and still say `agenttrace`.
