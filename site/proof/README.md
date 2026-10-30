Captured 2026-10-27 from a local verify stack (run 20261027-201809). Nothing here is edited; links in the outputs point at the local stack that produced them.

The page's proof (support dataset v2, 15 items):

- support-dataset.json: the 15 items. Items 1-4 are v1; 5-15 were added so the gate has the power to judge a drop.
- eval-baseline.txt: `agenttrace eval -c examples/demo-agent/eval.yaml` as main at the merge-base 27a9da5 (experiment 353eef3f).
- eval-pr.txt, pr-comment.md: the same with prompt-change.diff applied, from branch landing-page @ 8803625, `--base main --fail-on-regression --report-md` (experiment 440068ae). Exit 3: steps regressed 93% → 33%.
- trace-6a1896b9….jsonl (main) and trace-fa77e079….jsonl (PR): sample 0 of item 56518cd4, "Can I get a refund on order 1044?". The hero diagram draws these.
- add-to-dataset.json: the PR's failed trace fa77e079 added to the dataset ("Add to dataset", `POST /v1/datasets/<id>/items/from-trace`), then given the expected trajectory lookup_order(order_id="1044").
- replay-1044.txt: `agenttrace replay fa77e079… -c examples/demo-agent/eval.yaml --require-pass` with the prompt fixed. Exit 0, 3/3 runs pass (replay fcb911cf).
- demo-agent-1042.txt and trace-07a86a29….jsonl: `uv run demo-agent "Where is order 1042?"`, the span tree in "What your agent actually did".

v1/ holds the first proof on the 4-item dataset: the same prompt edit dropped steps 75% → 25%, but with 4 items the gate only flagged it underpowered (eval-pr.txt, pr-comment.md); a softer edit changed nothing (eval-pr-quiet.txt).
