# 8. A regression needs statistical significance and a threshold

Status: accepted

## Context

LLM outputs vary between runs. A CI gate that fails on any score drop fails on noise, and teams learn to ignore it.

## Decision

A metric regresses only when the 95% bootstrap confidence interval for its change versus the baseline excludes zero **and** the drop exceeds that metric's threshold in `eval.yaml`. Only metric regressions fail the check.

Item regressions (most samples passed in the baseline, most fail now) are listed in the PR comment but don't fail the check by default.

## Consequences

- Small datasets with few samples rarely produce significant results; the PR comment should say when a comparison is underpowered.
- Each sample is a resampling unit, which is why every item runs several samples (default 3).
