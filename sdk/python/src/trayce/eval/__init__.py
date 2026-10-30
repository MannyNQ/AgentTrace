"""Evals (ADR-0006): `trayce eval` runs an agent over a dataset version and uploads scores.

`Case` is what a custom scorer receives. This package imports nothing beyond the core SDK, so the
ingest service can use `trayce.eval.results` without the `eval` extra.
"""

from trayce.eval.scorers import Case

__all__ = ["Case"]
