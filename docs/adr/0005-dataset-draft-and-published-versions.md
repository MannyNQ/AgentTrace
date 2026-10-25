# 5. Datasets have a draft and immutable published versions

Status: accepted

## Context

Experiment results must stay reproducible, so the data they ran on can't change underneath them. But "Add to dataset" from a trace and hand edits happen often, and freezing a version on every edit would create noise.

## Decision

Each dataset has one editable draft. Publishing freezes the draft into the next immutable version (`v1`, `v2`, ...). Experiments always pin a dataset version. "Add to dataset" writes to the draft.

## Consequences

- An item added from a trace isn't tested in CI until someone publishes.
- Publishing can require every new item to have an `expected_output` or `expected_trajectory`.
