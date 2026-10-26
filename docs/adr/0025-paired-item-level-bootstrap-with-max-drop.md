# 25. Regressions use a paired item-level bootstrap and a per-scorer max_drop

Status: accepted. Amends ADR-0008.

## Context

ADR-0008 gates on a metric only when its drop is significant and larger than "that metric's threshold in `eval.yaml`", and it treats each sample as a resampling unit. Three things were left open or wrong:

- "Metric" wasn't defined.
- `eval.yaml` already has a per-scorer `threshold`: the pass threshold a score must reach. A second meaning for the same word would confuse every config.
- The samples of one item share an input and are correlated. Resampling them as independent units makes the interval too narrow, so noise reads as a regression, which is the failure ADR-0008 exists to prevent.

## Decision

- **Metric.** One per scorer: its pass rate, the share of samples scoring at or above the scorer's pass threshold. Cost and latency are shown in comparisons but don't gate.
- **Items compared.** For each scorer, only items present in both experiments (by `item_id`) and scored in both. An item is scored in a run unless that scorer skipped all its samples. The per-scorer denominator is shown.
- **Bootstrap.** Paired and item-level: each of 10,000 rounds draws the compared items with replacement and computes the pass-rate change from all of those items' samples in both runs. The 95% interval is the 2.5th to 97.5th percentile. The seed is fixed (ADR-0024).
- **`max_drop`.** Each scorer gets a `max_drop` field: the largest pass-rate drop in absolute points it tolerates, default `0.05`. A scorer regresses when the interval lies entirely below zero and the observed drop is larger than `max_drop`. `max_drop` is left out of the `eval.yaml` hash, so loosening the gate doesn't reset baselines.
- **Underpowered.** A metric is underpowered when its interval's half-width is larger than its `max_drop`: a drop of exactly `max_drop` couldn't be detected. It's flagged per scorer and doesn't change the gate.
- **Item regressions.** Per scorer: more than half of an item's samples passed in the baseline and more than half fail now. Listed, never gating.

## Consequences

- Fewer false regressions than a sample-level bootstrap, at the cost of wider intervals on small datasets. The underpowered flag makes that visible.
- `threshold` keeps one meaning (pass threshold), and the gate's tolerance has its own name.
