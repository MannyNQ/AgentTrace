"""Answering calls from a recording (ADR-0040): the playback's matching, and the tool hooks."""

import asyncio

import pytest

from agenttrace import call_tool, trace
from agenttrace._playback import (
    LlmRecording,
    NoRecording,
    Playback,
    ToolRecording,
    activate,
    deactivate,
    differing,
)


def playback(fork_index: int | None = 3, stub_tools: bool = False) -> Playback:
    return Playback(
        llm=[LlmRecording(0, {"model": "m", "system": "s"}, {"id": "msg_1"})],
        tools=[
            ToolRecording(1, "lookup", {"order": "42"}, {"status": "a"}),
            ToolRecording(2, "lookup", {"order": "42"}, {"status": "b"}),
            ToolRecording(4, "refund", {"order": "42"}, {"ok": True}),
        ],
        fork_index=fork_index,
        stub_tools=stub_tools,
    )


def test_each_recording_answers_one_call_in_order():
    p = playback()
    assert p.answer_llm({"model": "m", "system": "s"}) == {"id": "msg_1"}
    assert p.answer_tool("lookup", {"order": "42"}).output == {"status": "a"}
    assert p.answer_tool("lookup", {"order": "42"}).output == {"status": "b"}
    assert p.answer_llm({"model": "m"}) is None  # past the prefix: live
    assert p.live and p.diverged_at is None


def test_reaching_a_call_recorded_after_the_fork_point_goes_live_without_diverging():
    p = playback(fork_index=1)
    assert p.answer_tool("refund", {"order": "42"}) is None
    assert p.live and p.diverged_at is None


def test_a_call_the_recording_never_made_is_where_the_run_diverged():
    p = playback()
    p.answer_llm({"model": "m", "system": "s"})
    assert p.answer_tool("lookup", {"order": "7"}) is None
    assert (p.live, p.diverged_at) == (True, 1)


def test_a_differing_request_is_a_warning_not_a_divergence():
    p = playback()
    assert p.answer_llm({"model": "m", "system": "new"}) == {"id": "msg_1"}
    assert [(w.step, w.fields) for w in p.warnings] == [(0, ["system"])]
    assert differing({"a": 1, "b": 2}, {"a": 1, "c": 2}) == ["b", "c"]


def test_tool_stubbing_refuses_a_call_with_no_recording():
    p = playback(fork_index=None, stub_tools=True)
    assert p.answer_tool("refund", {"order": "42"}).output == {"ok": True}
    with pytest.raises(NoRecording):
        p.answer_tool("refund", {"order": "42"})  # its one recording is used
    assert p.failure is not None and "refund" in p.failure


def test_traced_and_async_tools_are_answered_without_running(collected):
    ran = []

    @trace(name="lookup", kind="tool")
    def lookup(order: str) -> dict:
        ran.append(order)
        return {}

    async def lookup_async(order: str) -> dict:
        ran.append(order)
        return {}

    token = activate(playback())
    try:
        assert lookup("42") == {"status": "a"}
        assert asyncio.run(call_tool("lookup", {"order": "42"}, lookup_async)) == {"status": "b"}
    finally:
        deactivate(token)
    assert ran == []
    assert all(s.attributes["agenttrace.stubbed"] == "true" for s in collected.spans)


def test_without_a_playback_tools_run(collected):
    assert call_tool("lookup", {"order": "42"}, lambda order: {"live": order}) == {"live": "42"}
    assert "agenttrace.stubbed" not in collected.spans[0].attributes
