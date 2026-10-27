"""Following `replay_of` from a replay run back to the original it re-ran (ADR-0040)."""

from agenttrace.eval.results import SpanRef
from agenttrace_ingest.trace_inputs import resolve_origin

A, B, C = ("a" * 32, "b" * 32, "c" * 32)


def ref(trace_id: str, span_id: str = "1" * 16) -> SpanRef:
    return SpanRef(trace_id=trace_id, span_id=span_id)


def lookup_from(roots: dict[str, tuple[str, SpanRef | None]]):
    return roots.get


def test_a_fork_of_a_fork_resolves_to_the_original_root():
    # C forked B at a step; B forked A at a step; A is a production trace.
    roots = {A: ("0" * 16, None), B: ("2" * 16, ref(A, "5" * 16)), C: ("3" * 16, ref(B))}
    assert resolve_origin(ref(B, "9" * 16), lookup_from(roots)) == ref(A, "0" * 16)


def test_an_expired_trace_ends_the_walk_at_the_last_known_reference():
    roots = {B: ("2" * 16, ref(A, "5" * 16))}
    assert resolve_origin(ref(B), lookup_from(roots)) == ref(A, "5" * 16)


def test_a_cycle_ends_the_walk():
    roots = {A: ("0" * 16, ref(B)), B: ("2" * 16, ref(A))}
    assert resolve_origin(ref(A), lookup_from(roots)).trace_id in (A, B)
