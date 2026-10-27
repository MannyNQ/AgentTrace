"""Planning a fork or a tool-stubbed replay from a recorded trace's steps (ADR-0040).

`GET /v1/traces/{id}/steps` gives every LLM and tool span with its full payloads and the marks
that say whether a recording can stand in for it. The rule for that lives here, beside the
interceptors that will answer the calls, so a fork that can't be played back faithfully is
refused before it starts, naming the step.
"""

from dataclasses import dataclass
from typing import Any

from agenttrace._playback import LlmRecording, Playback, ToolRecording
from agenttrace.eval.results import SpanRef

TOOL_HOOKS = ("call_tool", "trace")
EXACT_OUTPUTS = ("json", "text")


class ForkError(Exception):
    """The fork or tool stubbing can't be planned; the message names the step and why."""


@dataclass(frozen=True)
class Step:
    index: int
    span_id: str
    parent_span_id: str
    kind: str
    name: str
    status: str
    error_type: str
    error_message: str | None
    interceptor: str | None
    truncated: list[str]
    output_recorded: str | None
    input: Any
    output: Any

    @classmethod
    def parse(cls, d: dict[str, Any]) -> "Step":
        return cls(
            index=d["index"],
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id") or "",
            kind=d["kind"],
            name=d["name"],
            status=d["status"],
            error_type=d.get("error_type") or "",
            error_message=d.get("error_message"),
            interceptor=d.get("interceptor"),
            truncated=list(d.get("truncated") or []),
            output_recorded=d.get("output_recorded"),
            input=d.get("input"),
            output=d.get("output"),
        )

    @property
    def label(self) -> str:
        return f"step {self.index} ({self.kind} {self.name}, span {self.span_id})"


def why_not(s: Step) -> str | None:
    """Why the recording can't stand in for this step, or None when it can."""
    if s.kind == "llm":
        if s.interceptor != "anthropic":
            return "it wasn't recorded by instrument_anthropic(), so nothing can answer it"
        if s.truncated:
            return f"the SDK cut its {' and '.join(s.truncated)} to fit a request"
        if s.status == "error":
            return "the call failed, and a failed LLM call isn't recorded in full"
        if not isinstance(s.output, dict):
            return "it has no recorded response; it may predate Phase 9's SDK"
        return None
    if s.interceptor not in TOOL_HOOKS:
        return "it wasn't recorded by call_tool() or @trace(kind='tool'), so nothing can answer it"
    if "output" in s.truncated:
        return "the SDK cut its output to fit a request"
    if s.status != "error" and s.output_recorded not in EXACT_OUTPUTS:
        if s.output_recorded is None:
            return "it doesn't say how its output was recorded; it may predate Phase 9's SDK"
        return "its output didn't survive recording (a tuple, an object, or another non-JSON type)"
    return None


@dataclass(frozen=True)
class Plan:
    trace_id: str  # the trace whose steps are played back
    steps: list[Step]
    fork: Step | None
    stub_tools: bool

    @property
    def fork_of(self) -> SpanRef | None:
        return (
            None
            if self.fork is None
            else SpanRef(trace_id=self.trace_id, span_id=self.fork.span_id)
        )

    def playback(self) -> Playback:
        """A fresh one for each run, since each consumes its recordings."""
        end = None if self.fork is None else self.fork.index
        return Playback(
            llm=[
                LlmRecording(s.index, s.input, s.output)
                for s in self.steps
                if s.kind == "llm" and end is not None and s.index < end
            ],
            tools=[
                ToolRecording(
                    s.index,
                    s.name,
                    s.input,
                    s.output,
                    error_type=(s.error_type or "Error") if s.status == "error" else None,
                    error_message=s.error_message,
                )
                for s in self.steps
                if s.kind == "tool"
            ],
            fork_index=end,
            stub_tools=self.stub_tools,
        )


def _find(steps: list[Step], trace_id: str, fork_from: str) -> Step:
    found = [s for s in steps if s.span_id.startswith(fork_from.lower())]
    if not found:
        raise ForkError(
            f"{fork_from} isn't an LLM or tool span of trace {trace_id}; a fork starts at one"
        )
    if len(found) > 1:
        raise ForkError(f"{fork_from} matches {len(found)} steps; give more of the span ID")
    return found[0]


def plan(trace_id: str, raw: list[dict[str, Any]], fork_from: str | None, stub_tools: bool) -> Plan:
    """Raises ForkError naming the first step the recording can't stand in for."""
    steps = [Step.parse(d) for d in raw]
    fork = None if fork_from is None else _find(steps, trace_id, fork_from)
    if fork is not None and fork.interceptor is None:
        raise ForkError(
            f"{fork.label} wasn't recorded by an SDK hook, so a fork can't tell when the run gets "
            "there"
        )
    played = [s for s in steps if fork is not None and s.index < fork.index]
    if stub_tools:
        played += [s for s in steps if s.kind == "tool" and s not in played]
    tools = {s.span_id: s for s in steps if s.kind == "tool"}
    for s in sorted(played, key=lambda s: s.index):
        if (reason := why_not(s)) is not None:
            raise ForkError(f"{s.label} can't be played back: {reason}")
    for s in steps:
        parent = tools.get(s.parent_span_id)
        if parent is not None and parent in played:
            # The stub skips the tool's body, so the steps inside it never happen.
            raise ForkError(
                f"{s.label} runs inside tool {parent.label}; playing back tools that make their "
                "own LLM or tool calls isn't supported yet"
            )
    return Plan(trace_id=trace_id, steps=steps, fork=fork, stub_tools=stub_tools)
