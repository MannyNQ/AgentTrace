import asyncio
from dataclasses import dataclass

import pytest

from agenttrace import SpanKind, SpanStatus, span, trace


def test_nested_spans_share_a_trace_and_link_parents(collected):
    with span("run", "agent", input="hi", tags=["demo"], user_id="u1") as root:
        with span("lookup", "tool", input={"id": 1}) as tool:
            tool.set_output("found")
        root.set_output("done")

    run, lookup = collected.by_name("run"), collected.by_name("lookup")
    assert [s.name for s in collected.spans] == ["lookup", "run"]  # children end first
    assert lookup.trace_id == run.trace_id
    assert lookup.parent_span_id == run.span_id
    assert run.is_root and run.trace is not None
    assert run.trace.tags == ["demo"] and run.trace.user_id == "u1"
    assert lookup.kind == SpanKind.TOOL and lookup.input == {"id": 1} and lookup.output == "found"
    assert run.end_time is not None and run.end_time >= run.start_time


def test_trace_metadata_on_a_child_is_dropped(collected):
    with span("root"), span("child", tags=["x"]):
        pass
    assert collected.by_name("child").trace is None


def test_exception_marks_error_and_propagates(collected):
    with pytest.raises(KeyError), span("boom"):
        raise KeyError("x")
    s = collected.by_name("boom")
    assert s.status == SpanStatus.ERROR and s.error_type == "KeyError"


def test_separate_top_level_spans_are_separate_traces(collected):
    with span("a"):
        pass
    with span("b"):
        pass
    assert collected.spans[0].trace_id != collected.spans[1].trace_id


@dataclass
class Result:
    answer: str


def test_trace_decorator_records_arguments_and_return(collected):
    @trace(kind="agent")
    def ask(question: str, retries: int = 2) -> Result:
        return Result(answer=question.upper())

    assert ask("hi") == Result("HI")
    s = collected.spans[0]
    assert s.kind == SpanKind.AGENT and s.name.endswith("ask")
    assert s.input == {"question": "hi", "retries": 2}
    assert s.output == {"answer": "HI"}


def test_trace_decorator_on_async_functions_nests_across_awaits(collected):
    @trace
    async def child():
        await asyncio.sleep(0)
        return 1

    @trace(name="parent")
    async def parent():
        return await asyncio.gather(child(), child())

    asyncio.run(parent())
    p = collected.by_name("parent")
    children = [s for s in collected.spans if s.name != "parent"]
    assert len(children) == 2
    assert all(c.parent_span_id == p.span_id for c in children)


def test_unserializable_values_become_repr(collected):
    obj = object()
    with span("x", input={"o": obj}):
        pass
    assert collected.spans[0].input == {"o": repr(obj)}


def test_no_api_key_means_no_export_and_no_error(monkeypatch):
    from agenttrace import exporter

    monkeypatch.delenv("AGENTTRACE_API_KEY", raising=False)
    monkeypatch.setattr(exporter, "_config", None)
    monkeypatch.setattr(exporter, "_exporter", None)
    with span("quiet") as s:
        s.set_output(1)
    assert exporter._exporter is None


def test_span_processors_see_spans_without_an_exporter(monkeypatch):
    from agenttrace import add_span_processor, tracing

    monkeypatch.setattr(tracing, "get_exporter", lambda: None)
    seen, failing = [], []

    def broken(_):
        failing.append(1)
        raise RuntimeError("processor bug")

    remove_broken = add_span_processor(broken)
    remove = add_span_processor(seen.append)
    with span("root"), span("child", "tool"):
        pass
    remove()
    remove_broken()
    with span("after"):
        pass
    assert [s.name for s in seen] == ["child", "root"]
    assert len(failing) == 2  # a raising processor doesn't stop the others


def test_span_processors_run_before_export(collected):
    from agenttrace import add_span_processor

    seen = []
    remove = add_span_processor(lambda s: seen.append((s.name, len(collected.spans))))
    try:
        with span("one"):
            pass
    finally:
        remove()
    assert seen == [("one", 0)] and [s.name for s in collected.spans] == ["one"]
