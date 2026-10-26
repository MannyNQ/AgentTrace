import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import type { ToolCall } from "@/lib/datasets";
import {
  comparingScorer,
  getExperiment,
  getExperimentItem,
  type ComparingMode,
  type Sample,
} from "@/lib/experiments";
import { liveTraces } from "@/lib/traces";
import { align, type Alignment } from "@/lib/trajectory";
import { formatCost, formatDuration } from "@/lib/format";
import { Crumbs, SourceTrace } from "../../../../datasets/ui";
import { SampleTrace, experimentLabel } from "../../../ui";

type Props = PageProps<"/experiments/[id]/items/[itemId]">;

export default function ExperimentItemPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading item…</p>}>
        <ItemView params={props.params} />
      </Suspense>
    </main>
  );
}

function Json({ value, label }: { value: unknown; label: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre
      aria-label={label}
      className="mt-1 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-line/40 p-2 font-mono text-xs"
    >
      {text}
    </pre>
  );
}

async function ItemView({ params }: Pick<Props, "params">) {
  await connection();
  const { id, itemId } = await params;
  const [e, detail] = await Promise.all([getExperiment(id), getExperimentItem(id, itemId)]);
  if (!e || !detail) notFound();
  const { item, samples } = detail;
  const c = item.contents;
  const traceIds = [...samples.flatMap((s) => (s.trace_id ? [s.trace_id] : [])), ...(item.source_trace_id ? [item.source_trace_id] : [])];
  const live = await liveTraces(traceIds);
  const comparing = c.expected_trajectory ? comparingScorer(e.scorers) : null;
  const thresholds = new Map(e.scorers.map((s) => [s.name, s.threshold]));

  return (
    <>
      <Crumbs trail={[["Experiments", "/experiments"], [experimentLabel(e), `/experiments/${e.id}`]]} />
      <h1 className="mt-2 text-xl font-semibold tracking-tight">Item</h1>
      <p className="mt-1 font-mono text-xs text-muted">
        {item.item_id} · rev {item.rev} · source <SourceTrace item={item} live={live} />
      </p>

      <section className="mt-6 grid gap-4 md:grid-cols-2" aria-label="Test case">
        <div>
          <h2 className="text-sm font-medium">Input</h2>
          <Json value={c.input} label="Input" />
        </div>
        <div className="space-y-3">
          <div>
            <h2 className="text-sm font-medium">Expected output</h2>
            {c.expected_output === null ? (
              <p className="mt-1 text-xs text-muted">none: output scorers skip this item</p>
            ) : (
              <Json value={c.expected_output} label="Expected output" />
            )}
          </div>
          <div>
            <h2 className="text-sm font-medium">Expected trajectory</h2>
            {c.expected_trajectory ? (
              <ol className="mt-1 space-y-1 font-mono text-xs">
                {c.expected_trajectory.map((t, i) => (
                  <li key={i}>
                    {i + 1}. <Call call={t} />
                  </li>
                ))}
              </ol>
            ) : (
              <p className="mt-1 text-xs text-muted">none: trajectory comparisons skip this item</p>
            )}
          </div>
        </div>
      </section>

      <section className="mt-8" aria-label="Samples">
        <h2 className="font-medium">Samples</h2>
        {comparing && (
          <p className="mt-1 text-xs text-muted">
            Tool calls are lined up the way <span className="font-mono">{comparing.name}</span> compares them (
            {comparing.mode.replace("_", " ")}); the first place they part is marked.
          </p>
        )}
        {samples.length === 0 ? (
          <p className="mt-3 text-sm text-muted">No samples uploaded for this item yet.</p>
        ) : (
          <ul className="mt-3 space-y-4">
            {samples.map((s) => (
              <SampleCard
                key={s.sample_index}
                sample={s}
                expected={c.expected_trajectory}
                mode={comparing?.mode ?? null}
                thresholds={thresholds}
                live={live}
              />
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

/** `tool(key="value", n=3)`: arguments as written in a call, values as JSON. */
function Call({ call }: { call: ToolCall }) {
  const args = Object.entries(call.args ?? {});
  return (
    <>
      <span className="font-medium">{call.tool}</span>
      {args.length > 0 && (
        <span className="text-muted">
          ({args.map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
        </span>
      )}
    </>
  );
}

function SampleCard({
  sample: s,
  expected,
  mode,
  thresholds,
  live,
}: {
  sample: Sample;
  expected: ToolCall[] | null;
  mode: ComparingMode | null;
  thresholds: Map<string, number>;
  live: Set<string> | null;
}) {
  const failed = s.error !== null || s.scores.some((x) => !x.passed);
  return (
    <li
      className={`rounded-md border p-3 ${failed ? "border-bad/50" : "border-line"}`}
      data-sample={s.sample_index}
      data-failed={failed}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
        <h3 className="font-medium">Sample #{s.sample_index}</h3>
        <span className="font-mono text-xs text-muted">{formatDuration(s.latency_ms)}</span>
        <span className="font-mono text-xs text-muted">{formatCost(s.cost_usd)}</span>
        <span className="ml-auto">
          <SampleTrace traceId={s.trace_id} live={live} />
        </span>
      </div>

      {s.error && (
        <p className="mt-2 font-mono text-xs text-bad" data-sample-error>
          {s.error}
        </p>
      )}

      <ul className="mt-2 space-y-1 text-xs" aria-label={`Scores for sample ${s.sample_index}`}>
        {s.scores.length === 0 && <li className="text-muted">No scorer applies to this item.</li>}
        {s.scores.map((x) => (
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

      <div className="mt-3 space-y-4">
        <div>
          <h4 className="text-xs font-medium text-muted">Output</h4>
          {s.error ? (
            <p className="mt-1 text-xs text-muted">none: the agent raised</p>
          ) : (
            <>
              <Json value={s.output} label={`Output of sample ${s.sample_index}`} />
              {s.output_truncated && (
                <p className="mt-1 text-xs text-muted">
                  Cut to fit storage; the scorers saw the whole output.
                </p>
              )}
            </>
          )}
        </div>
        <Trajectory actual={s.trajectory} expected={expected} mode={mode} />
      </div>
    </li>
  );
}

function Trajectory({
  actual,
  expected,
  mode,
}: {
  actual: ToolCall[];
  expected: ToolCall[] | null;
  mode: ComparingMode | null;
}) {
  if (!expected || !mode) {
    return (
      <div>
        <h4 className="text-xs font-medium text-muted">Tool calls</h4>
        <CallList calls={actual} empty="no tool calls" />
      </div>
    );
  }
  const a = align(mode, expected, actual);
  return (
    <div data-trajectory-compare data-mode={mode}>
      <h4 className="text-xs font-medium text-muted">Tool calls: expected vs actual</h4>
      <div className="mt-1 grid gap-4 sm:grid-cols-2">
        <Side title="Expected" calls={expected} side="expected" alignment={a} />
        <Side title="Actual" calls={actual} side="actual" alignment={a} />
      </div>
    </div>
  );
}

function CallList({ calls, empty }: { calls: ToolCall[]; empty: string }) {
  if (calls.length === 0) return <p className="mt-1 text-xs text-muted">{empty}</p>;
  return (
    <ol className="mt-1 space-y-1 font-mono text-xs">
      {calls.map((t, i) => (
        <li key={i} className="break-words">
          {i + 1}. <Call call={t} />
        </li>
      ))}
    </ol>
  );
}

function Side({
  title,
  calls,
  side,
  alignment,
}: {
  title: string;
  calls: ToolCall[];
  side: "expected" | "actual";
  alignment: Alignment;
}) {
  const links = alignment[side];
  const first = alignment.firstMismatch;
  return (
    <div className="min-w-0">
      <p className="text-[11px] uppercase tracking-wider text-muted">{title}</p>
      {calls.length === 0 && <p className="text-xs text-muted">no tool calls</p>}
      <ol className="space-y-1 font-mono text-xs">
        {calls.map((t, i) => {
          const match = links[i];
          const isFirst = first?.side === side && first.index === i;
          const label =
            match !== null
              ? `matches ${side === "expected" ? "actual" : "expected"} #${match + 1}`
              : side === "expected"
                ? "missing"
                : "extra";
          return (
            <li
              key={i}
              className={`break-words rounded px-1 ${isFirst ? "bg-bad/10 ring-1 ring-bad" : ""}`}
              data-call={i}
              data-match={match ?? "none"}
              data-first-mismatch={isFirst || undefined}
            >
              <span className={match !== null ? "text-good" : side === "expected" ? "text-bad" : "text-muted"}>
                {match !== null ? "✓" : side === "expected" ? "✗" : "+"}
              </span>{" "}
              {i + 1}. <Call call={t} />
              <span className="block text-[11px] text-muted">
                {label}
                {isFirst && <span className="font-medium text-bad"> · first mismatch</span>}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
