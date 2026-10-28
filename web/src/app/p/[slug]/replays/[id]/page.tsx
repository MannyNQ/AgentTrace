import Link from "next/link";
import { notFound, unstable_rethrow } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import type { ToolCall } from "@/lib/datasets";
import { getReplay, type ReplayRun } from "@/lib/replays";
import { clickHouseError, getTrace, liveTraces, traceOutcome } from "@/lib/traces";
import { prefixToolCalls, steps } from "@/lib/forks";
import { firstDifference } from "@/lib/trajectory";
import { formatCost, formatDuration, formatTime } from "@/lib/format";
import { Crumbs } from "../../datasets/ui";
import { Call, Json } from "../../experiments/samples";
import { SampleTrace, StatusBadge } from "../../experiments/ui";
import { PassCounts, ReplayCode, ReplayKind } from "../ui";
import { projectLinks } from "@/lib/links";

type Props = PageProps<"/p/[slug]/replays/[id]">;

export default function ReplayPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading replay…</p>}>
        <ReplayView params={props.params} />
      </Suspense>
    </main>
  );
}

/** The original as read from ClickHouse: null once it has expired, or why it couldn't be read. */
type Original = Awaited<ReturnType<typeof traceOutcome>> | { error: string };

/** Where a fork went live in the trace it forked, read from that trace's spans (ADR-0040). */
type ForkPoint = { step: number; kind: string; name: string; prefixTools: number } | null;

async function forkPoint(traceId: string, spanId: string): Promise<ForkPoint> {
  const trace = await getTrace(traceId).catch((e) => {
    unstable_rethrow(e);
    return null;
  });
  const fork = trace && steps(trace.spans).find((s) => s.spanId === spanId);
  const prefixTools = trace && prefixToolCalls(trace.spans, spanId);
  return fork && prefixTools !== null ? { step: fork.index, kind: fork.kind, name: fork.name, prefixTools } : null;
}

/**
 * The trace the runs re-ran and every replay run side by side, each run's first difference
 * marked. A fork's runs are compared with the trace it forked, whose steps they played back; the
 * played-back prefix is folded away and the fork point marked (ADR-0040).
 */
async function ReplayView({ params }: Pick<Props, "params">) {
  await connection();
  const { id } = await params;
  const r = await getReplay(id);
  if (!r) notFound();
  const originalId = r.replay_of.trace_id;
  const comparedId = r.fork_of?.trace_id ?? originalId;
  const [original, live, fork] = await Promise.all([
    traceOutcome(comparedId).catch((e): Original => {
      unstable_rethrow(e); // an expired session redirects to /login
      return { error: clickHouseError(e) };
    }),
    liveTraces(r.replay_runs.flatMap((run) => (run.trace_id ? [run.trace_id] : []))),
    r.fork_of ? forkPoint(r.fork_of.trace_id, r.fork_of.span_id) : null,
  ]);
  const thresholds = new Map(r.scorers.map((s) => [s.name, s.threshold]));
  const expected = r.item?.contents.expected_trajectory ?? null;

  return (
    <>
      <Crumbs
        trail={[
          ["Traces", "/traces"],
          [`trace ${originalId.slice(0, 8)}…`, `/traces/${originalId}`],
        ]}
      />
      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <h1 className="text-xl font-semibold tracking-tight">{r.fork_of ? "Fork" : "Replay"}</h1>
        <StatusBadge status={r.status} />
        <ReplayKind r={r} />
        <span className="font-mono text-xs text-muted">{formatTime(new Date(r.started_at))}</span>
      </div>
      <p className="mt-1 font-mono text-xs text-muted">{r.id}</p>
      {r.error && <p className="mt-2 font-mono text-xs text-bad">{r.error}</p>}
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
        {r.fork_of && (
          <div data-fork-point={r.fork_of.span_id}>
            <dt className="text-xs text-muted">Forked at</dt>
            <dd className="font-mono text-xs">
              {fork ? `step ${fork.step} (${fork.kind} ${fork.name})` : `span ${r.fork_of.span_id.slice(0, 8)}`}
              <span className="text-muted"> of trace {r.fork_of.trace_id.slice(0, 8)}…</span>
              {fork && <span className="block text-muted">{fork.step} steps played back, live from there</span>}
            </dd>
          </div>
        )}
        {r.tool_mode === "recorded" && (
          <div>
            <dt className="text-xs text-muted">Tools</dt>
            <dd className="text-xs">answered from the recording; none ran</dd>
          </div>
        )}
        <div>
          <dt className="text-xs text-muted">Code</dt>
          <dd>
            <ReplayCode r={r} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Runs</dt>
          <dd className="font-mono">
            {r.runs_uploaded}/{r.runs}
            {r.errored > 0 && <span className="text-bad"> · {r.errored} errored</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Cost</dt>
          <dd className="font-mono">{formatCost(r.cost_usd, r.unpriced_runs)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Scored against</dt>
          <dd className="font-mono text-xs" data-scored-item={r.item_id ?? undefined}>
            {r.item_id ? `item ${r.item_id.slice(0, 8)} rev ${r.item_rev}` : "nothing (unscored)"}
            {r.item_id && !r.item && <span className="text-muted"> (deleted from the draft since)</span>}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Passed</dt>
          <dd>
            <PassCounts r={r} />
          </dd>
        </div>
      </dl>
      {expected && (
        <section className="mt-4" aria-label="Expected trajectory">
          <h2 className="text-xs font-medium text-muted">Expected tool calls (the item&apos;s draft revision)</h2>
          <CallList calls={expected} />
        </section>
      )}

      <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <OriginalSide traceId={comparedId} original={original} forked={r.fork_of !== null} />
        <section aria-label="Replay runs" data-side="runs">
          <h2 className="font-medium">Replay runs</h2>
          {r.replay_runs.length === 0 ? (
            <p className="mt-2 text-sm text-muted">No runs uploaded.</p>
          ) : (
            <ul className="mt-3 space-y-4">
              {r.replay_runs.map((run) => (
                <RunCard
                  key={run.run_index}
                  run={run}
                  // A run that left the recording ran live from there, so its prefix isn't all played back.
                  prefix={run.diverged_at === null ? (fork?.prefixTools ?? null) : null}
                  original={original && !("error" in original) ? original.trajectory : null}
                  thresholds={thresholds}
                  live={live}
                />
              ))}
            </ul>
          )}
        </section>
      </div>
    </>
  );
}

async function OriginalSide({ traceId, original, forked }: { traceId: string; original: Original; forked: boolean }) {
  const at = await projectLinks();
  return (
    <section aria-label="Original" data-side="original">
      <h2 className="font-medium">
        {forked ? "Forked trace" : "Original"}{" "}
        <Link href={at(`/traces/${traceId}`)} className="font-mono text-xs underline">
          trace {traceId.slice(0, 8)}…
        </Link>
      </h2>
      {!original ? (
        <p className="mt-2 text-sm text-muted" data-original-expired>
          The {forked ? "forked" : "original"} trace has expired, so there&apos;s nothing to compare the runs with.
        </p>
      ) : "error" in original ? (
        <p className="mt-2 text-sm" data-original-error>
          <span className="font-medium text-bad">Couldn&apos;t read the original trace from ClickHouse.</span>{" "}
          <span className="text-muted">{original.error}</span>
        </p>
      ) : (
        <div className="mt-3 space-y-4 rounded-md border border-line p-3">
          <div>
            <h3 className="text-xs font-medium text-muted">Output</h3>
            {original.rootError ? (
              <p className="mt-1 font-mono text-xs text-bad">raised {original.rootError}</p>
            ) : (
              <Json value={original.output} label="Original output" />
            )}
          </div>
          <div>
            <h3 className="text-xs font-medium text-muted">Tool calls</h3>
            <CallList calls={original.trajectory} />
          </div>
        </div>
      )}
    </section>
  );
}

function CallItem({ call, i, first }: { call: ToolCall; i: number; first?: number | null }) {
  return (
    <li
      className={`break-words rounded px-1 ${first === i ? "bg-bad/10 ring-1 ring-bad" : ""}`}
      data-call={i}
      data-first-difference={first === i || undefined}
    >
      {i + 1}. <Call call={call} />
      {first === i && <span className="block text-[11px] font-medium text-bad">first difference</span>}
    </li>
  );
}

/**
 * A trajectory, its first difference marked. A fork run's first `prefix` calls were played back
 * from the recording, so they're folded under one line, opened when the difference is among them.
 */
function CallList({ calls, first, prefix }: { calls: ToolCall[]; first?: number | null; prefix?: number | null }) {
  if (calls.length === 0) return <p className="mt-1 text-xs text-muted">no tool calls</p>;
  const played = Math.min(prefix ?? 0, calls.length);
  return (
    <>
      {played > 0 && (
        <details className="mt-1 text-xs" open={first != null && first < played} data-played-back={played}>
          <summary className="cursor-pointer select-none text-muted">
            {played} tool {played === 1 ? "call" : "calls"} played back from the recording
          </summary>
          <ol className="mt-1 space-y-1 font-mono text-xs opacity-70">
            {calls.slice(0, played).map((c, i) => (
              <CallItem key={i} call={c} i={i} first={first} />
            ))}
          </ol>
        </details>
      )}
      {prefix != null && (
        <p className="mt-1 border-t border-dashed border-line pt-1 text-[11px] font-medium text-muted" data-fork-marker>
          fork point · live from here
        </p>
      )}
      <ol className="mt-1 space-y-1 font-mono text-xs">
        {calls.slice(played).map((c, j) => (
          <CallItem key={played + j} call={c} i={played + j} first={first} />
        ))}
      </ol>
    </>
  );
}

function RunCard({
  run,
  original,
  thresholds,
  live,
  prefix,
}: {
  run: ReplayRun;
  original: ToolCall[] | null;
  thresholds: Map<string, number>;
  live: Set<string> | null;
  prefix: number | null;
}) {
  const failed = run.error !== null || run.scores.some((x) => !x.passed);
  const first = original ? firstDifference(original, run.trajectory) : null;
  return (
    <li
      className={`rounded-md border p-3 ${failed ? "border-bad/50" : "border-line"}`}
      data-run={run.run_index}
      data-failed={failed}
      data-first-difference={first ?? "none"}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        <h3 className="font-medium">Run #{run.run_index}</h3>
        <span className="font-mono text-xs text-muted">{formatDuration(run.latency_ms)}</span>
        <span className="font-mono text-xs text-muted">{formatCost(run.cost_usd)}</span>
        <span className="ml-auto">
          <SampleTrace traceId={run.trace_id} live={live} />
        </span>
      </div>
      {run.error && <p className="mt-2 font-mono text-xs text-bad">{run.error}</p>}
      {run.diverged_at !== null && (
        <p className="mt-2 text-xs text-bad" data-diverged-at={run.diverged_at}>
          Left the recording at step {run.diverged_at}: it made a tool call the recording never made, so it ran live
          from there.
        </p>
      )}
      {run.warnings.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-muted" aria-label={`Request warnings for run ${run.run_index}`}>
          {run.warnings.map((w) => (
            <li key={w.step} data-warning-step={w.step}>
              Step {w.step}: the request&apos;s <span className="font-mono">{w.fields.join(", ")}</span> differed from
              the recording; the recorded reply was still used.
            </li>
          ))}
        </ul>
      )}
      {run.scores.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs" aria-label={`Scores for run ${run.run_index}`}>
          {run.scores.map((x) => (
            <li key={x.scorer} data-score={x.scorer} data-passed={x.passed}>
              <span className={x.passed ? "text-good" : "text-bad"}>{x.passed ? "✓" : "✗"}</span>{" "}
              <span className="font-mono font-medium">{x.scorer}</span>{" "}
              <span className="font-mono text-muted">
                {x.score.toFixed(2)}
                {(thresholds.get(x.scorer) ?? 1) < 1 && ` (passes at ≥ ${thresholds.get(x.scorer)})`}
              </span>
              {x.reason && <span className="text-muted"> · {x.reason}</span>}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        <div className="min-w-0">
          <h4 className="text-xs font-medium text-muted">Output</h4>
          {run.error ? (
            <p className="mt-1 text-xs text-muted">none: the agent raised</p>
          ) : (
            <>
              <Json value={run.output} label={`Output of run ${run.run_index}`} />
              {run.output_truncated && <p className="mt-1 text-xs text-muted">Cut to fit storage.</p>}
            </>
          )}
        </div>
        <div className="min-w-0">
          <h4 className="text-xs font-medium text-muted">
            Tool calls
            {original &&
              (first === null ? (
                <span className="font-normal"> · same as the original</span>
              ) : (
                <span className="font-normal text-bad"> · differ from the original at step {first + 1}</span>
              ))}
          </h4>
          <CallList calls={run.trajectory} first={first} prefix={prefix} />
        </div>
      </div>
    </li>
  );
}
