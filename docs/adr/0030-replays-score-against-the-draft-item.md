# 30. Replays are scored against the trace's draft dataset item

Status: accepted. Builds on ADR-0001 and ADR-0020.

## Context

A replay always shows each run beside the original, with the first difference marked. A difference says the agent behaved differently, not that it now behaves correctly: the original is a recording of a failure, not an expectation. ADR-0001's loop turns a failed trace into a dataset item with an expectation, and Add to dataset is where a user writes down what should have happened.

Other places to get the expectation:

- **A published dataset version.** Publishing is the step after the fix works. Requiring it first slows the loop and puts unproven expectations into versions CI runs.
- **Flags on `agenttrace replay`.** A second place to write expectations, outside the dataset, that CI never sees.
- **No scoring.** The user judges the side-by-side by eye, which doesn't answer "does my fix work" for a trajectory with many steps.

## Decision

- The CLI looks in `eval.yaml`'s dataset for the draft item whose `source_trace_id` is the original trace. A trace is in a dataset's draft at most once, so there is at most one. When the replayed trace is itself a replay, the lookup uses the original (ADR-0028).
- If that item has an expected output or an expected trajectory, each replay run is scored with `eval.yaml`'s scorers against the item's current draft revision, exactly as a sample is: scorers lacking their expectation are skipped, and an errored run scores 0. The CLI prints pass counts per scorer, and the replay records the revision (`item_id`, `rev`) it used.
- No linked draft item, no expectation, or no `eval.yaml`: the replay is unscored. It still shows the side-by-side.
- `--require-pass` exits 3 when the scored runs don't all pass. With nothing to score it exits 2 before running and says why, so a gate can't pass by scoring nothing.

## Consequences

- The fix loop is: Add to dataset, set an expectation, replay until it passes, publish, and let the CI gate keep it passing.
- Editing the draft item changes what the next replay is scored against. The recorded revision keeps older replays readable.
- The CI gate reads experiments only, so a passing replay doesn't replace publishing and running `eval`.
- Replays report pass counts only. There's no baseline, bootstrap, or regression: those belong to experiments over a published version (ADR-0025).
- A replay scored with an `llm_judge` needs the judge's credentials, as `eval` does (ADR-0027).
