"""The eval runner (ADR-0006, ADR-0022): run the entry point over every item of a dataset version
`samples` times, score each sample, and stream the results to the server (ADR-0023).

Each sample runs under a root span the runner opens, so concurrent samples never share a trace,
and a span processor collects that trace's spans as they end: the tool spans are the trajectory,
the LLM spans the token usage. Sync entry points run on a thread pool and async ones on the
event loop, both bounded by `concurrency`. A timed-out sync sample can't be stopped; its thread
is abandoned and `abandoned` (on `RunResult` or `RunError`) tells the caller to exit without
waiting for it. A run that stops never waits for samples still running: their timeouts no longer
apply once the run has stopped, so waiting could hang.

Scoring runs on its own thread pool, under the same `concurrency` limit as the agent calls, since
the LLM judge calls a model (ADR-0027).

Uploads are batched by size as well as count, since ingest refuses requests over 5 MB, and every
sample is shrunk to fit one request: its output to 1 MB and its tool-call arguments to a budget.
Scorers see everything in full; only the uploaded copy is cut.
"""

import asyncio
import contextvars
import inspect
import json
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agenttrace._json import to_jsonable
from agenttrace.eval import scorers as sc
from agenttrace.eval.client import ApiError, Client, DatasetVersion, VersionItem
from agenttrace.eval.config import EvalConfig
from agenttrace.eval.results import (
    MAX_SAMPLES_PER_REQUEST,
    AgentRun,
    Finish,
    ModelUsage,
    NewExperiment,
    SampleResult,
    Score,
    ToolCall,
)
from agenttrace.exporter import flush
from agenttrace.spans import Span, SpanKind
from agenttrace.tracing import add_span_processor, span

SAMPLE_SPAN = "agenttrace.eval.sample"
EVAL_TAG = "agenttrace-eval"
MAX_OUTPUT_BYTES = 1024 * 1024  # what the server stores (ADR-0023)
MAX_ERROR = 1000
UNKNOWN_MODEL = "unknown"  # an LLM span without a model: the server can't price it
UPLOAD_BATCH = 20
# Ingest refuses requests over 5 MB (ADR-0017); stay under it with room for the envelope.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_ARG_BYTES = 4 * 1024  # one tool-call argument value, as uploaded
MAX_TRAJECTORY_BYTES = 1024 * 1024  # all of a sample's tool-call arguments, as uploaded


class StartError(Exception):
    """The server refused to create the experiment, so nothing was recorded."""


class RunError(Exception):
    """The run can't go on: the experiment is (or would be) `failed`. `abandoned` means agent
    threads may still be running, so the caller shouldn't wait for them at exit."""

    def __init__(self, message: str, abandoned: bool = False):
        super().__init__(message)
        self.abandoned = abandoned


@dataclass(frozen=True)
class Git:
    commit: str | None = None
    branch: str | None = None
    merge_base: str | None = None  # on a pull request (ADR-0026)
    base_branch: str | None = None


@dataclass
class RunResult:
    experiment_id: str
    summary: dict[str, Any]  # the server's experiment detail, after finishing
    abandoned: bool = False  # a timed-out sync sample's thread is still running


class Capture:
    """Spans of the traces being run, by trace id, as they end."""

    def __init__(self):
        self.lock = threading.Lock()
        self.spans: dict[str, list[Span]] = defaultdict(list)
        self.watching: set[str] = set()

    def watch(self, trace_id: str) -> None:
        with self.lock:
            self.watching.add(trace_id)

    def __call__(self, s: Span) -> None:
        with self.lock:
            if s.trace_id in self.watching:
                self.spans[s.trace_id].append(s)

    def take(self, trace_id: str) -> list[Span]:
        """The trace's spans so far; spans that end later (an abandoned sample's) are dropped."""
        with self.lock:
            self.watching.discard(trace_id)
            return self.spans.pop(trace_id, [])


def trajectory(spans: list[Span]) -> list[ToolCall]:
    """Tool spans in start order (GLOSSARY.md). Arguments are the span's input when it's an
    object."""
    tools = sorted((s for s in spans if s.kind == SpanKind.TOOL), key=lambda s: s.start_time)
    return [
        ToolCall(tool=s.name, args=s.input if isinstance(s.input, dict) else None) for s in tools
    ]


def usage(spans: list[Span]) -> list[ModelUsage]:
    totals: dict[str, dict[str, int]] = {}
    for s in spans:
        if s.kind != SpanKind.LLM:
            continue
        model = s.model if s.model and s.input_tokens is not None else UNKNOWN_MODEL
        t = totals.setdefault(model, defaultdict(int))
        for f in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_5m_tokens",
            "cache_write_1h_tokens",
        ):
            t[f] += getattr(s, f) or 0
    return [ModelUsage(model=m, **t) for m, t in totals.items()]


def _clean(text: str) -> str:
    return text.replace("\x00", "�")  # Postgres text and jsonb can't hold NUL


def _without_nul(value: Any) -> Any:
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        return {_clean(k): _without_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_without_nul(v) for v in value]
    return value


def storable_output(value: Any) -> tuple[Any, bool]:
    """The output as the server can store it, and whether it was cut: JSON without NaN or NUL,
    at most 1 MB, else its JSON text cut to fit (marked truncated)."""
    value = to_jsonable(value)
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ValueError:  # NaN or Infinity: keep the JSON text, as a string
        value = text = json.dumps(value, ensure_ascii=False)
    if "\\u0000" in text:  # json.dumps writes NUL as an escape
        value = _without_nul(value)
        text = json.dumps(value, ensure_ascii=False)
    data = text.encode()
    if len(data) <= MAX_OUTPUT_BYTES - 1024:
        return value, False
    return data[: MAX_OUTPUT_BYTES // 2].decode(errors="ignore"), True


def _cut_arg(value: Any) -> Any:
    """A tool-call argument as uploaded: NUL replaced, and over `MAX_ARG_BYTES` of JSON cut to a
    string prefix ending in an ellipsis."""
    value = _without_nul(to_jsonable(value))
    text = json.dumps(value, ensure_ascii=False)
    if len(text.encode()) <= MAX_ARG_BYTES:
        return value
    return text.encode()[: MAX_ARG_BYTES - 3].decode(errors="ignore") + "…"


def storable_trajectory(calls: list[ToolCall]) -> list[ToolCall]:
    """The trajectory as uploaded: every call and tool name kept, NUL replaced, each argument
    value cut to `MAX_ARG_BYTES`, and once the calls' arguments pass `MAX_TRAJECTORY_BYTES` the
    remaining calls keep their names only."""
    out, budget = [], MAX_TRAJECTORY_BYTES
    for call in calls:
        args = None
        if call.args is not None:
            cut = {_clean(k): _cut_arg(v) for k, v in call.args.items()}
            size = len(json.dumps(cut, ensure_ascii=False).encode())
            if size <= budget:
                args, budget = cut, budget - size
            else:
                budget = 0
        out.append(ToolCall(tool=_clean(call.tool), args=args))
    return out


class UploadBuffer:
    """Collects samples into batches that ingest accepts: at most `UPLOAD_BATCH` samples and
    `MAX_UPLOAD_BYTES` of JSON. Every sample fits on its own (outputs and trajectories are
    capped), so a batch never has to be split."""

    def __init__(self, max_count: int = UPLOAD_BATCH, max_bytes: int = MAX_UPLOAD_BYTES):
        self.max_count, self.max_bytes = max_count, max_bytes
        self.pending: list[AgentRun] = []
        self.size = 0

    def add(self, r: AgentRun) -> list[AgentRun] | None:
        """Adds a sample; returns a full batch to upload first, if adding it would overflow."""
        size = len(r.model_dump_json().encode()) + 1
        ready = None
        if self.pending and (
            len(self.pending) >= self.max_count or self.size + size > self.max_bytes
        ):
            ready = self.rest()
        self.pending.append(r)
        self.size += size
        return ready

    def rest(self) -> list[AgentRun]:
        batch, self.pending, self.size = self.pending, [], 0
        return batch


def git_from_env(env: dict[str, str]) -> Git:
    """What GitHub Actions says about the run; locally, the CLI falls back to asking git. On a
    pull request `GITHUB_SHA` is GitHub's temporary merge commit, so the commit is the PR head
    from the event payload (ADR-0026). The merge-base needs git, so the CLI computes it."""
    branch = env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME") or None
    commit = env.get("GITHUB_SHA") or None
    if env.get("GITHUB_EVENT_NAME", "").startswith("pull_request") and env.get("GITHUB_EVENT_PATH"):
        try:
            with open(env["GITHUB_EVENT_PATH"]) as f:
                commit = json.load(f)["pull_request"]["head"]["sha"]
        except (OSError, ValueError, KeyError, TypeError):
            pass  # keep GITHUB_SHA rather than nothing
    return Git(commit=commit, branch=branch, base_branch=env.get("GITHUB_BASE_REF") or None)


def _run_in(ctx: contextvars.Context, fn: Callable[[Any], Any], arg: Any) -> Any:
    return ctx.run(fn, arg)


@dataclass
class _Sample:
    item: VersionItem
    index: int
    trace_id: str
    started_at: datetime
    latency_ms: float
    output: Any = None
    error: str | None = None
    timed_out_thread: bool = False
    spans: list[Span] = field(default_factory=list)


def _case(item: VersionItem, s: _Sample, calls: list[ToolCall]) -> sc.Case:
    c = item.contents
    expected = c.get("expected_trajectory")
    return sc.Case(
        input=c["input"],
        expected_output=c.get("expected_output"),
        expected_trajectory=None if expected is None else [ToolCall(**t) for t in expected],
        metadata=c.get("metadata") or {},
        output=s.output,
        trajectory=calls,
    )


@dataclass
class Scored:
    scores: list[Score] = field(default_factory=list)
    judge_usage: list[ModelUsage] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # "<scorer>: <why>", for the run's output


def _sum_usage(usage: list[ModelUsage]) -> list[ModelUsage]:
    totals: dict[str, dict[str, int]] = {}
    for u in usage:
        t = totals.setdefault(u.model, defaultdict(int))
        for k, v in u.model_dump(exclude={"model"}).items():
            t[k] += v
    return [ModelUsage(model=m, **t) for m, t in totals.items()]


# Custom scorers are called one at a time across the run (`Scorer.thread_safe`).
_serial = threading.Lock()


def _grade(scorer: sc.Scorer, case: sc.Case) -> sc.Result | None:
    if scorer.thread_safe:
        return scorer.grade(case)
    with _serial:
        return scorer.grade(case)


def score(scorers: list[sc.Scorer], case: sc.Case, error: str | None) -> Scored:
    """Skipped scorers give no score; on an errored sample every other scorer gives 0. A `Fatal`
    scorer error propagates and stops the run."""
    out, usage = Scored(), []
    for scorer in scorers:
        if not scorer.applies(case):
            continue
        if error is not None:
            result = sc.fail(f"agent raised {error.split(':', 1)[0]}")
        else:
            try:
                result = _grade(scorer, case)
            except sc.Fatal:
                raise
            except sc.Skipped as e:
                usage += e.usage
                out.skipped.append(f"{scorer.name}: {e}")
                continue
            except Exception as e:  # a scorer bug grades the sample 0, and says so
                result = sc.fail(f"scorer failed: {type(e).__name__}: {e}")
            if result is None:
                continue
            usage += result.usage
        reason = None if result.reason is None else _clean(result.reason)
        out.scores.append(Score(scorer=scorer.name, score=result.score, reason=reason))
    out.judge_usage = _sum_usage(usage)
    return out


class AgentRunner:
    """Runs the entry point over jobs (an item and a repeat index), scores each run, and uploads
    the results in batches. Subclasses say what each run's root span carries, what record a run
    becomes, and where records go: `Runner` for experiments, `ReplayRunner` for replays."""

    root_span = SAMPLE_SPAN
    tag = EVAL_TAG

    def __init__(
        self,
        concurrency: int,
        timeout_s: float,
        entry: Callable[[Any], Any],
        scorers: list[sc.Scorer],
        thresholds: dict[str, float],
        report: Callable[[str], None] = print,
    ):
        self.concurrency, self.timeout_s = concurrency, timeout_s
        self.entry, self.scorers, self.thresholds = entry, scorers, thresholds
        self.report = report
        self.capture = Capture()
        self.is_async = inspect.iscoroutinefunction(entry)

    # --- What subclasses decide ---

    def _metadata(self, run_id: str, item: VersionItem, index: int) -> dict[str, str]:
        raise NotImplementedError

    def _attributes(self) -> dict[str, str] | None:
        return None

    def _record(self, s: _Sample, fields: dict[str, Any]) -> AgentRun:
        """The uploaded record: `fields` are the ones every agent run has."""
        raise NotImplementedError

    def _upload(self, run_id: str, batch: list[Any]) -> None:
        raise NotImplementedError

    def _label(self, s: _Sample) -> str:
        return f"item {s.item.item_id[:8]} #{s.index}"

    # --- Shared ---

    async def _run_one(
        self, run_id: str, item: VersionItem, index: int, sem: asyncio.Semaphore,
        pool: ThreadPoolExecutor, scoring: ThreadPoolExecutor,
    ) -> tuple[_Sample, AgentRun, list[str]]:  # fmt: skip
        """Runs one sample and scores it, both within one `concurrency` slot."""
        async with sem:
            meta = self._metadata(run_id, item, index)
            started_at, t0 = datetime.now(UTC), time.monotonic()
            error, output, abandoned = None, None, False
            inp = item.contents["input"]
            with span(
                self.root_span,
                "agent",
                input=inp,
                tags=[self.tag],
                metadata=meta,
                attributes=self._attributes(),
            ) as root:
                self.capture.watch(root.trace_id)
                try:
                    if self.is_async:
                        call = self.entry(inp)
                    else:
                        ctx = contextvars.copy_context()  # the agent's spans nest under root
                        loop = asyncio.get_running_loop()
                        call = loop.run_in_executor(pool, _run_in, ctx, self.entry, inp)
                    output = await asyncio.wait_for(call, self.timeout_s)
                    root.set_output(output)
                except TimeoutError:
                    error = f"TimeoutError: no result after {self.timeout_s:g} s"
                    abandoned = not self.is_async
                    root.set_error("TimeoutError")
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"[:MAX_ERROR]
                    root.set_error(type(e).__name__)
            s = _Sample(
                item=item,
                index=index,
                trace_id=root.trace_id,
                started_at=started_at,
                latency_ms=(time.monotonic() - t0) * 1000,
                output=output,
                error=None if error is None else _clean(error),
                timed_out_thread=abandoned,
                spans=self.capture.take(root.trace_id),
            )
            result, skipped = await asyncio.get_running_loop().run_in_executor(
                scoring, self._result, s
            )
            return s, result, skipped

    def _result(self, s: _Sample) -> tuple[AgentRun, list[str]]:
        calls = trajectory(s.spans)
        scored = score(self.scorers, _case(s.item, s, calls), s.error)
        output, truncated = (None, False) if s.error else storable_output(s.output)
        fields = dict(
            trace_id=s.trace_id,
            started_at=s.started_at,
            latency_ms=s.latency_ms,
            output=output,
            output_truncated=truncated,
            trajectory=storable_trajectory(calls),
            error=s.error,
            usage=usage(s.spans),
            judge_usage=scored.judge_usage,
            scores=scored.scores,
        )
        return self._record(s, fields), scored.skipped

    def _line(self, done: int, total: int, s: _Sample, r: AgentRun, skipped: list[str]) -> str:
        marks = " ".join(
            f"{x.scorer}:{'pass' if x.score >= self.thresholds[x.scorer] else 'FAIL'}"
            for x in r.scores
        )
        status = f"error ({s.error})" if s.error else marks or "no scorer applies"
        skips = "".join(f"  (skipped {x})" for x in skipped)
        return f"[{done}/{total}] {self._label(s)}  {status}{skips}"

    async def _runs(self, run_id: str, jobs: list[tuple[VersionItem, int]]) -> bool:
        """Runs and uploads every job; returns whether a sync run's thread was abandoned. If
        this raises, runs still going are left to finish on their own."""
        sem = asyncio.Semaphore(self.concurrency)
        uploads = UploadBuffer()
        abandoned, finished = False, False
        # Not a `with` block: leaving one waits for every thread, abandoned ones included.
        pool = ThreadPoolExecutor(self.concurrency, thread_name_prefix="agenttrace-eval")
        # Scorers get their own threads, so an abandoned agent thread can't hold up scoring.
        scoring = ThreadPoolExecutor(self.concurrency, thread_name_prefix="agenttrace-score")
        tasks = [
            asyncio.ensure_future(self._run_one(run_id, item, i, sem, pool, scoring))
            for item, i in jobs
        ]
        try:
            for done, next_run in enumerate(asyncio.as_completed(tasks), 1):
                s, r, skipped = await next_run
                abandoned |= s.timed_out_thread
                self.report(self._line(done, len(jobs), s, r, skipped))
                batch = uploads.add(r)
                if batch:
                    await asyncio.to_thread(self._upload, run_id, batch)
            if uploads.pending:
                await asyncio.to_thread(self._upload, run_id, uploads.rest())
            finished = True
        finally:
            for t in tasks:
                t.cancel()
            # Waiting is only safe when every run has returned or timed out cleanly.
            pool.shutdown(wait=finished and not abandoned, cancel_futures=True)
            scoring.shutdown(wait=finished, cancel_futures=True)
        return abandoned

    def _execute(
        self, run_id: str, jobs: list[tuple[VersionItem, int]], fail: Callable[[str], None]
    ) -> bool:
        """Runs the jobs with spans captured, then flushes them so each run's trace is there to
        open from the results. Anything that stops the run calls `fail` and raises RunError."""
        assert UPLOAD_BATCH <= MAX_SAMPLES_PER_REQUEST
        # A truncated output's text doubles at worst when encoded again (escaped quotes).
        assert 2 * MAX_OUTPUT_BYTES + MAX_TRAJECTORY_BYTES < MAX_UPLOAD_BYTES
        remove = add_span_processor(self.capture)
        try:
            abandoned = asyncio.run(self._runs(run_id, jobs))
        except BaseException as e:  # Ctrl-C included: the record mustn't stay `running`
            fail(f"{type(e).__name__}: {e}")
            raise RunError(f"the run stopped: {type(e).__name__}: {e}", abandoned=True) from e
        finally:
            remove()
        flush(timeout_s=10)
        return abandoned


class Runner(AgentRunner):
    """Runs an experiment: every item of a dataset version, `samples` times."""

    def __init__(
        self,
        cfg: EvalConfig,
        client: Client,
        entry: Callable[[Any], Any],
        scorers: list[sc.Scorer],
        report: Callable[[str], None] = print,
    ):
        thresholds = {s.name: s.threshold for s in cfg.scorers}
        super().__init__(cfg.concurrency, cfg.timeout_s, entry, scorers, thresholds, report)
        self.cfg, self.client = cfg, client

    def _metadata(self, run_id: str, item: VersionItem, index: int) -> dict[str, str]:
        return {"experiment_id": run_id, "item_id": item.item_id, "sample": str(index)}

    def _record(self, s: _Sample, fields: dict[str, Any]) -> SampleResult:
        return SampleResult(item_id=uuid.UUID(s.item.item_id), sample_index=s.index, **fields)

    def _upload(self, run_id: str, batch: list[Any]) -> None:
        self.client.upload_samples(run_id, batch)

    def run(self, version: DatasetVersion, git: Git, name: str | None = None) -> RunResult:
        """Creates the experiment, runs it, and finishes it. Anything that stops the run after
        the experiment exists finishes it as `failed` and raises RunError."""
        experiment_id = str(uuid.uuid4())
        try:
            self._create(experiment_id, version, git, name)
        except ApiError as e:
            raise StartError(f"couldn't create the experiment: {e}") from e
        jobs = [(item, i) for item in version.items for i in range(self.cfg.samples)]
        abandoned = self._execute(experiment_id, jobs, lambda e: self._fail(experiment_id, e))
        self.client.finish(experiment_id, Finish(status="completed"))
        return RunResult(experiment_id, self.client.experiment(experiment_id), abandoned)

    def _create(
        self, experiment_id: str, version: DatasetVersion, git: Git, name: str | None
    ) -> None:
        self.client.create_experiment(
            NewExperiment(
                id=uuid.UUID(experiment_id),
                dataset_id=uuid.UUID(version.dataset_id),
                dataset_version=version.version,
                name=name or self.cfg.name,
                samples=self.cfg.samples,
                scorers=[s.spec() for s in self.cfg.scorers],
                config=self.cfg.as_json(),
                config_hash=self.cfg.hash(),
                git_commit=git.commit,
                git_branch=git.branch,
                git_merge_base=git.merge_base,
                git_base_branch=git.base_branch,
            )
        )

    def _fail(self, experiment_id: str, error: str) -> None:
        try:
            self.client.finish(
                experiment_id, Finish(status="failed", error=_clean(error)[:MAX_ERROR])
            )
        except Exception as e:  # the server may be why the run failed
            self.report(f"could not mark experiment {experiment_id} failed: {e}")
