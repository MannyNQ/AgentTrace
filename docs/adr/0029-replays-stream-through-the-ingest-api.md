# 29. Replays are their own entity and stream through the ingest API

Status: accepted. Builds on ADR-0023; follows ADR-0021 on retention.

## Context

A replay (ADR-0028) produces a group of runs whose results the trace page and the replay page need: when it ran, at which commit, each run's output, trajectory, cost, and scores. Like an experiment, it's created by a CLI that can crash or retry, and its cost has to be computed once on the server (ADR-0010).

Recording a replay as a one-item experiment would reuse `/v1/experiments` as it is, but:

- An experiment always pins a dataset version (ADR-0023). Most replayed traces aren't in any published version.
- Replays would fill the experiments list, and a completed one with the right `eval.yaml` hash at the merge-base commit could be chosen as a baseline (ADR-0026).

## Decision

- Replays live in Postgres, in a `replays` table and a table of its runs, written through `/v1/replays` in the ingest service with API keys, like `/v1/experiments`.
- The lifecycle mirrors ADR-0023. The CLI generates the replay ID, creates it as `running`, upserts each replay run with its scores as soon as it's scored (keyed by replay ID and run index), and finishes it as `completed`, or `failed` when the run stops. Problems found before the run starts create nothing. The server computes each run's cost from the price table on arrival. Runs are capped and batched by bytes as samples are, through the same uploader.
- A replay records the original's root span (`replay_of`), git commit, branch, a dirty flag for uncommitted changes, the `eval.yaml` hash and scorer configs when there is an `eval.yaml`, and the item revision (`item_id`, `rev`) it was scored against, if any (ADR-0030).
- ClickHouse's `traces` summary gets a `replay_of` column, filled from the root span, so "replays of this trace" and "hide replay traces" are each one query.
- Replaying doesn't pin the original, and replay traces expire normally, as experiment traces do (ADR-0021). The trace list hides replay traces by default, behind a "Show replays" toggle.

## Consequences

- The experiments list and baseline selection never see replays.
- A replay's scores, outputs, and trajectories survive the expiry of its traces and of the original. When the original has expired, the replay page shows "trace expired" for it.
- A second results API to keep in step with the first. Shared pieces (the uploader, caps, cost on arrival) are reused, not copied.
- The trace list's default view filters on one column of `traces`, not on a lookup in Postgres.
