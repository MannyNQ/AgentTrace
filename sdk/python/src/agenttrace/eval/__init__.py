"""Evals (ADR-0006): `agenttrace eval` runs an agent over a dataset version and uploads scores.

`Case` is what a custom scorer receives. This package imports nothing beyond the core SDK, so the
ingest service can use `agenttrace.eval.results` without the `eval` extra.
"""

from agenttrace.eval.scorers import Case

__all__ = ["Case"]
