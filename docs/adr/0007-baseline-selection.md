# 7. Baseline selection: merge-base first

Status: accepted. Refined by ADR-0026.

## Context

A pull request's experiment needs a run on `main` to compare against. Comparing with the newest `main` mixes in changes the PR doesn't contain, and a missing baseline must not block first-time setup.

## Decision

The baseline is the newest completed experiment at the PR's merge-base commit with the same `eval.yaml` hash. If none exists, it's the newest completed experiment on `main` with that hash. If neither exists, there is no comparison: the PR comment reports scores only, and the check passes.

Experiments record git commit, branch, dataset version, `eval.yaml` hash, each scorer's config, and an optional name. Experiments with different `eval.yaml` hashes are never compared.

Comparisons use only dataset items (by stable `item_id`) present in both runs; added and removed items are listed separately.

## Consequences

- `main` needs an eval run on every merge for merge-base baselines to exist.
- Changing `eval.yaml` resets comparisons until `main` has a run with the new hash.
