"""Answering a replay run's calls from a recorded trace (ADR-0040).

A fork plays the original's steps back up to its fork point: each LLM call and tool call before
it gets the recorded result instead of running, and from the fork point on everything runs live.
Tool stubbing answers every tool call from the recording while LLM calls run live. The eval runner
sets one `Playback` per replay run in a context variable; the interceptors (the Anthropic
integration, `call_tool`, and `@trace` tools) ask it before making a call. A call made where the
context doesn't reach, such as a thread started without copying it, runs live.
"""

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from trayce._json import to_jsonable

STUBBED_ATTRIBUTE = "trayce.stubbed"  # on a span answered from the recording


class RecordedToolError(Exception):
    """A stubbed tool call that failed in the recording. The original exception's class isn't
    recorded, only its name and message, so this stands in for it."""

    def __init__(self, type_name: str, message: str | None):
        super().__init__(message or type_name)
        self.recorded_type = type_name


class NoRecording(Exception):
    """A tool call under tool stubbing that the recording has no answer for. It errors the run
    even if the agent catches it, since running the tool would repeat its side effects."""


@dataclass(frozen=True)
class LlmRecording:
    index: int  # the step's position in the recorded trace
    request: dict[str, Any]
    response: dict[str, Any]


@dataclass(frozen=True)
class ToolRecording:
    index: int
    name: str
    args: Any
    output: Any
    error_type: str | None = None  # set when the recorded call failed
    error_message: str | None = None


@dataclass(frozen=True)
class RequestWarning:
    """A prefix LLM request that differed from its recording; the stub was still returned."""

    step: int
    fields: list[str]


@dataclass
class Playback:
    """One run's answers. `fork_index` is the fork point's step index, or None when nothing is
    played back before going live (tool stubbing alone). Matching follows ADR-0040: prefix LLM
    calls by order, tool calls by name and arguments, each recording used once."""

    llm: list[LlmRecording]  # the prefix's LLM steps, in order
    tools: list[ToolRecording]  # every recorded tool step, in order
    fork_index: int | None
    stub_tools: bool
    live: bool = False
    diverged_at: int | None = None
    warnings: list[RequestWarning] = field(default_factory=list)
    failure: str | None = None  # why the run must error, even if the agent swallowed it
    _played: int = 0  # steps answered or reached so far, the run's own step count
    _llm_calls: int = 0
    _used: set[int] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        if self.fork_index is None:
            self.live = True

    def answer_llm(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """The recorded response for this call, or None to make it live."""
        with self._lock:
            k = self._llm_calls
            self._llm_calls += 1
            if self.live or k >= len(self.llm):
                self.live = True
                return None
            rec = self.llm[k]
            if fields := differing(to_jsonable(request), rec.request):
                self.warnings.append(RequestWarning(step=self._played, fields=fields))
            self._played += 1
            return rec.response

    def answer_tool(self, name: str, args: Any) -> ToolRecording | None:
        """The recording to answer this call with, or None to run it. Raises NoRecording under
        tool stubbing when nothing recorded matches."""
        args = to_jsonable(args)
        with self._lock:
            if not self.live:
                assert self.fork_index is not None
                rec = self._match(name, args, before=self.fork_index)
                if rec is not None:
                    self._played += 1
                    return rec
                # A call recorded at or after the fork point means the run got there; one the
                # recording never made means the prefix left it.
                if self._match(name, args, before=None, take=False) is None:
                    self.diverged_at = self._played
                self.live = True
            if not self.stub_tools:
                return None
            rec = self._match(name, args, before=None)
            if rec is None:
                self.failure = (
                    f"NoRecording: the recording has no {name} call with these arguments, and "
                    f"--stub-tools doesn't run tools: {args!r}"[:500]
                )
                raise NoRecording(self.failure)
            return rec

    def _match(
        self, name: str, args: Any, before: int | None, take: bool = True
    ) -> ToolRecording | None:
        for rec in self.tools:
            if rec.index in self._used or (before is not None and rec.index >= before):
                continue
            if rec.name == name and rec.args == args:
                if take:
                    self._used.add(rec.index)
                return rec
        return None


def differing(request: Any, recorded: Any) -> list[str]:
    """The top-level request fields that differ from the recording's, sorted."""
    if not isinstance(request, dict) or not isinstance(recorded, dict):
        return [] if request == recorded else ["request"]
    keys = request.keys() | recorded.keys()
    return sorted(k for k in keys if request.get(k) != recorded.get(k))


_current: ContextVar[Playback | None] = ContextVar("trayce_playback", default=None)


def current() -> Playback | None:
    return _current.get()


def activate(p: Playback | None):
    """Make `p` the playback for this context; returns the token to reset it with."""
    return _current.set(p)


def deactivate(token) -> None:
    _current.reset(token)
