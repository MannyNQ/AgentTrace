# 24. Comparisons are computed in ingest, and the web app calls it

Status: accepted. Amends ADR-0018.

## Context

Phase 4 compares an experiment with its baseline in two places: the PR comment, which the CLI renders in CI, and the compare view in the web UI. The comparison selects a baseline (ADR-0007), runs a bootstrap (ADR-0025), and lists item regressions. If the CLI and the web app each implemented it, in Python and TypeScript, the PR comment and the UI could disagree about the same pair of experiments.

ADR-0018 has the web app read the stores directly until auth exists.

## Decision

- Ingest serves `GET /v1/experiments/{id}/comparison`, authenticated with API keys like the rest of `/v1/experiments` (ADR-0023). With `?baseline=<id>` it compares against that experiment; without it, it selects the baseline per ADR-0007 and says how (merge-base or base-branch fallback), or reports that there is none.
- Both experiments must be `completed` and have the same `eval.yaml` hash, or the endpoint returns 409. This holds for a baseline picked by hand too.
- The bootstrap uses a fixed seed, so the same pair always gives the same result.
- The CLI and the web app both call this endpoint. The web app calls it from server components; for everything else it keeps reading the stores directly.

## Consequences

- The PR comment and the UI always show the same numbers.
- The web app gets its first read through ingest, ahead of the Phase 6 move to a single enforcement point for auth.
- Comparison logic is tested once, in Python.
