# 23. Experiments stream through the ingest API with client-generated IDs

Status: accepted. Builds on ADR-0019.

## Context

The eval runner produces an experiment and many samples, often in CI, where a run can crash or a request can be retried. ADR-0007 compares only completed experiments, so a run's status has to be recorded. Cost is computed once on the server (ADR-0010), and experiment traces expire (ADR-0021), so cost can't be read from ClickHouse later.

## Decision

- Results go to `/v1/experiments` in the ingest service, authenticated with API keys like `/v1/datasets` (ADR-0019), and live in Postgres.
- The runner generates the experiment ID. It creates the experiment as `running` when the run starts, uploads each sample with its scores as soon as it's scored, and finishes it as `completed`, or `failed` when the run stops after that (the server became unreachable, the runner crashed). Problems found before the run starts (an invalid `eval.yaml`, an unknown dataset or version, an entry point or scorer that won't import) exit without creating anything. A failing agent doesn't fail the experiment (ADR-0022).
- Sample uploads are upserts keyed by `(experiment_id, item_id, sample_index)`, so retries are no-ops.
- Each sample carries its `trace_id`, output, trajectory, latency, and token counts per model. The server computes its cost from the price table on arrival and stores it with the sample.
- Outputs are capped at 1 MB of JSON, like dataset items; a larger output is stored as a truncated prefix and marked truncated. Tool-call arguments are cut to 4 KB per value and 1 MB per sample, keeping every call's tool name. Scorers see both in full; only the stored copy is cut.
- The runner batches uploads by size as well as count (at most 4 MB, under ingest's 5 MB request limit), so a run with large outputs isn't refused.
- Every experiment records its dataset version (resolved to a number at start), git commit and branch when available, each scorer's config, and the `eval.yaml` hash: SHA-256 of the parsed config, defaults filled in, serialized as canonical JSON, so formatting, comments, and spelled-out defaults don't change it. The experiment's `name`, `concurrency`, and dataset `version` are left out: the first two don't change what's measured, and comparisons already use the items both runs share (ADR-0007).

## Consequences

- A crashed CI run leaves a visible `failed` or stuck `running` experiment instead of nothing, and the UI can show progress.
- Experiment cost survives the expiry of its traces.
- Phase 4's baseline and regression logic has everything it needs from Phase 3 experiments.
- The CLI exits 0 when the experiment completes, whatever the scores (Phase 3 has no score gate), 1 when it's `failed`, and 2 when the run couldn't start.
