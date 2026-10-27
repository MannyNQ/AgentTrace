import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import type { ToolCall } from "@/lib/datasets";
import { getReplay, type ReplayRun } from "@/lib/replays";
import { clickHouseError, liveTraces, traceOutcome } from "@/lib/traces";
import { firstDifference } from "@/lib/trajectory";
import { formatCost, formatDuration, formatTime } from "@/lib/format";
import { Crumbs } from "../../datasets/ui";
import { Call, Json } from "../../experiments/samples";
import { SampleTrace, StatusBadge } from "../../experiments/ui";
import { PassCounts, ReplayCode } from "../ui";

type Props = PageProps<"/replays/[id]">;

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

/** The original trace and every replay run side by side, each run's first difference marked. */
async function ReplayView({ params }: Pick<Props, "params">) {
  await connection();
  const { id } = await params;
  const r = await getReplay(id);
  if (!r) notFound();
  const originalId = r.replay_of.trace_id;
  const [original, live] = await Promise.all([
    traceOutcome(originalId).catch((e): Original => ({ error: clickHouseError(e) })),
    liveTraces(r.replay_runs.flatMap((run) => (run.trace_id ? [run.trace_id] : []))),
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
        <h1 className="text-xl font-semibold tracking-tight">Replay</h1>
        <StatusBadge status={r.status} />
        <span className="font-mono text-xs text-muted">{formatTime(new Date(r.started_at))}</span>
      </div>
      <p className="mt-1 font-mono text-xs text-muted">{r.id}</p>
      {r.error && <p className="mt-2 font-mono text-xs text-bad">{r.error}</p>}
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
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
        <OriginalSide traceId={originalId} original={original} />
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

function OriginalSide({ traceId, original }: { traceId: string; original: Original }) {
  return (
    <section aria-label="Original" data-side="original">
      <h2 className="font-medium">
        Original{" "}
        <Link href={`/traces/${traceId}`} className="font-mono text-xs underline">
          trace {traceId.slice(0, 8)}…
        </Link>
      </h2>
      {!original ? (
        <p className="mt-2 text-sm text-muted" data-original-expired>
          The original trace has expired, so there&apos;s nothing to compare the runs with.
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

function CallList({ calls, first }: { calls: ToolCall[]; first?: number | null }) {
  if (calls.length === 0) return <p className="mt-1 text-xs text-muted">no tool calls</p>;
  return (
    <ol className="mt-1 space-y-1 font-mono text-xs">
      {calls.map((t, i) => (
        <li
          key={i}
          className={`break-words rounded px-1 ${first === i ? "bg-bad/10 ring-1 ring-bad" : ""}`}
          data-call={i}
          data-first-difference={first === i || undefined}
        >
          {i + 1}. <Call call={t} />
          {first === i && <span className="block text-[11px] font-medium text-bad">first difference</span>}
        </li>
      ))}
    </ol>
  );
}

function RunCard({
  run,
  original,
  thresholds,
  live,
}: {
  run: ReplayRun;
  original: ToolCall[] | null;
  thresholds: Map<string, number>;
  live: Set<string> | null;
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
          <CallList calls={run.trajectory} first={first} />
        </div>
      </div>
    </li>
  );
}
