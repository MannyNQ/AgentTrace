import Link from "next/link";
import { notFound, unstable_rethrow } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { clickHouseError, flattenTree, getTrace, type SpanRow, type TraceSummary } from "@/lib/traces";
import { formatCost, formatDuration, formatTime, prettyPayload } from "@/lib/format";
import { listDatasets } from "@/lib/datasets";
import { AddToDatasetForm } from "@/app/p/[slug]/datasets/forms";
import { listReplays } from "@/lib/replays";
import { ForkCommand, ReplayCommand, ReplayList } from "@/app/p/[slug]/replays/ui";
import { forkCheck } from "@/lib/forks";
import { KindBadge, StatusBadge } from "../ui";
import { Crumbs } from "../../datasets/ui";
import { projectLinks } from "@/lib/links";

type Props = PageProps<"/p/[slug]/traces/[traceId]">;

export default function TracePage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Traces", "/traces"]]} />
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading trace…</p>}>
        <TraceView params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

async function TraceView({ params, searchParams }: Pick<Props, "params" | "searchParams">) {
  const at = await projectLinks();
  await connection();
  const { traceId } = await params;
  const { span: selectedId } = await searchParams;
  let trace;
  try {
    trace = await getTrace(traceId);
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return (
      <p className="mt-6 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Couldn&apos;t read the trace from ClickHouse.</span>{" "}
        <span className="text-muted" data-store-error>
          {clickHouseError(e)}
        </span>
      </p>
    );
  }
  if (!trace) notFound();

  const { summary, spans } = trace;
  const nodes = flattenTree(spans);
  const selected = spans.find((s) => s.spanId === selectedId) ?? nodes[0]?.span;
  const root = spans.find((s) => s.parentSpanId === "");
  const fork = selected && (selected.kind === "llm" || selected.kind === "tool") ? forkCheck(spans, selected.spanId) : null;

  return (
    <>
      <Header summary={summary} />
      <Suspense>
        <DatasetLinks traceId={traceId} />
      </Suspense>
      <Suspense>
        <Replays traceId={traceId} replayOf={summary.replayOf} rootAttributes={root?.attributes ?? {}} />
      </Suspense>
      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ol className="divide-y divide-line rounded-md border border-line" aria-label="Span tree">
          {nodes.map(({ span, depth }) => (
            <li key={span.spanId} data-span-id={span.spanId} data-kind={span.kind}>
              <Link
                href={at(`/traces/${traceId}?span=${span.spanId}`)}
                scroll={false}
                aria-current={span.spanId === selected?.spanId ? "true" : undefined}
                className="block px-3 py-2 text-sm hover:bg-line/40 aria-[current=true]:bg-line/60"
              >
                <div className="flex items-center gap-2" style={{ paddingLeft: depth * 16 }}>
                  <KindBadge kind={span.kind} />
                  <span className="min-w-0 truncate">{span.name}</span>
                  {span.status === "error" && (
                    <span className="font-mono text-xs text-bad" data-error>
                      ✗ {span.errorType}
                    </span>
                  )}
                  <span className="ml-auto shrink-0 font-mono text-xs text-muted">
                    {span.endTime ? formatDuration(span.endTime.getTime() - span.startTime.getTime()) : "—"}
                  </span>
                </div>
                <Timeline span={span} summary={summary} depth={depth} />
              </Link>
            </li>
          ))}
        </ol>
        {selected && (
          <SpanDetail span={selected} payloadHref={(which) => at(`/api/traces/${traceId}/spans/${selected.spanId}/${which}`)}>
            {fork && <ForkCommand traceId={traceId} spanId={selected.spanId} refused={fork.ok ? null : fork.reason} />}
          </SpanDetail>
        )}
      </div>
    </>
  );
}

function Header({ summary }: { summary: TraceSummary }) {
  const facts: [string, string][] = [
    ["Started", formatTime(summary.startTime)],
    ["Latency", formatDuration(summary.latencyMs)],
    ["Spans", String(summary.spanCount)],
    ["Cost", formatCost(summary.costUsd, summary.unpricedLlmSpans)],
    ["Failed spans", String(summary.errorCount)],
  ];
  if (summary.userId) facts.push(["User", summary.userId]);
  if (summary.sessionId) facts.push(["Session", summary.sessionId]);
  return (
    <header className="mt-2">
      <div className="flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{summary.rootName}</h1>
        <StatusBadge status={summary.status} />
      </div>
      <p className="mt-1 break-all font-mono text-xs text-muted">{summary.traceId}</p>
      <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 text-sm">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs text-muted">{k}</dt>
            <dd className="font-mono">{v}</dd>
          </div>
        ))}
        {summary.tags.length > 0 && (
          <div>
            <dt className="text-xs text-muted">Tags</dt>
            <dd>{summary.tags.join(", ")}</dd>
          </div>
        )}
      </dl>
    </header>
  );
}

/** Add to dataset (GLOSSARY.md). Archived datasets aren't offered. */
async function DatasetLinks({ traceId }: { traceId: string }) {
  let datasets;
  try {
    datasets = await listDatasets();
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return (
      <p className="mt-4 text-sm text-muted">
        Datasets unavailable: {e instanceof Error ? e.message : String(e)}
      </p>
    );
  }
  return (
    <section className="mt-4" aria-label="Datasets">
      <AddToDatasetForm traceId={traceId} datasets={datasets.map((d) => ({ id: d.id, name: d.name }))} />
    </section>
  );
}

/** `<trace_id>:<span_id>` as a replay run's root span carries it, or null. Any SDK client can
 * set the attribute, so only well-formed IDs become links. */
function parseRef(value: string | undefined): { traceId: string; spanId: string } | null {
  const m = /^([0-9a-f]{32}):([0-9a-f]{16})$/.exec(value ?? "");
  return m ? { traceId: m[1], spanId: m[2] } : null;
}

/**
 * A replay run links to what it re-ran: its original, or for a fork (ADR-0040) the step it forked
 * and the original at the start of the chain. Any trace lists its replays and the forks of it; a
 * replay run's replays are forks of it, since replaying one whole replays its original.
 */
async function Replays({
  traceId,
  replayOf,
  rootAttributes,
}: {
  traceId: string;
  replayOf: string;
  rootAttributes: Record<string, string>;
}) {
  const at = await projectLinks();
  const forkedAt = parseRef(rootAttributes["trayce.replay_of"]);
  const origin = parseRef(rootAttributes["trayce.replay_origin"]);
  const lineage = !replayOf ? null : origin && forkedAt ? (
    <p className="mt-4 text-sm" data-fork-of={`${forkedAt.traceId}:${forkedAt.spanId}`}>
      A fork of{" "}
      <Link href={at(`/traces/${forkedAt.traceId}?span=${forkedAt.spanId}`)} className="font-mono underline">
        trace {forkedAt.traceId.slice(0, 8)}… at span {forkedAt.spanId.slice(0, 8)}
      </Link>
      {origin.traceId !== forkedAt.traceId && (
        <>
          , which re-ran{" "}
          <Link href={at(`/traces/${origin.traceId}`)} className="font-mono underline">
            trace {origin.traceId.slice(0, 8)}…
          </Link>
        </>
      )}
      . Replaying it whole replays the original; forking it plays this run back.
    </p>
  ) : (
    <p className="mt-4 text-sm" data-replay-of={replayOf}>
      A replay run of{" "}
      <Link href={at(`/traces/${replayOf}`)} className="font-mono underline">
        trace {replayOf.slice(0, 8)}…
      </Link>
      . Replaying it replays that trace.
    </p>
  );
  let replays;
  try {
    replays = await listReplays(traceId);
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return (
      <>
        {lineage}
        <p className="mt-4 text-sm text-muted">Replays unavailable: {e instanceof Error ? e.message : String(e)}</p>
      </>
    );
  }
  if (replayOf) {
    return (
      <>
        {lineage}
        {replays.length > 0 && (
          <section className="mt-4" aria-label="Forks of this run">
            <h2 className="text-sm font-medium">Forks of this run</h2>
            <ReplayList replays={replays} />
          </section>
        )}
      </>
    );
  }
  return (
    <section className="mt-6" aria-label="Replays">
      <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
        <h2 className="text-sm font-medium">Replays</h2>
        <ReplayCommand traceId={traceId} />
      </div>
      {replays.length === 0 ? (
        <p className="mt-2 text-xs text-muted">Not replayed yet. Select an LLM or tool span to fork from it.</p>
      ) : (
        <ReplayList replays={replays} />
      )}
    </section>
  );
}

/** Where the span sits inside the trace's time range. */
function Timeline({ span, summary, depth }: { span: SpanRow; summary: TraceSummary; depth: number }) {
  const total = Math.max(summary.lastEndTime.getTime() - summary.startTime.getTime(), 1);
  const left = ((span.startTime.getTime() - summary.startTime.getTime()) / total) * 100;
  const end = (span.endTime ?? summary.lastEndTime).getTime();
  const width = Math.max(((end - span.startTime.getTime()) / total) * 100, 0.5);
  return (
    <div className="mt-1 h-1 rounded bg-line/50" style={{ marginLeft: depth * 16 }} aria-hidden>
      <div
        className={`h-1 rounded ${span.status === "error" ? "bg-bad" : "bg-muted"}`}
        style={{ marginLeft: `${left}%`, width: `${Math.min(width, 100 - left)}%` }}
      />
    </div>
  );
}

function SpanDetail({
  span,
  payloadHref,
  children,
}: {
  span: SpanRow;
  payloadHref: (which: "input" | "output") => string;
  children?: React.ReactNode;
}) {
  const facts: [string, string][] = [
    ["Kind", span.kind],
    ["Status", span.status + (span.errorType ? ` (${span.errorType})` : "")],
    ["Duration", span.endTime ? formatDuration(span.endTime.getTime() - span.startTime.getTime()) : "—"],
  ];
  if (span.kind === "llm") {
    facts.push(
      ["Model", span.model || "—"],
      ["Tokens", `${span.inputTokens ?? "?"} in · ${span.outputTokens ?? "?"} out`],
      ...(span.cacheReadTokens || span.cacheWriteTokens
        ? [["Cache", `${span.cacheReadTokens ?? 0} read · ${span.cacheWriteTokens ?? 0} written`] as [string, string]]
        : []),
      ["Cost", formatCost(span.costUsd)],
    );
  }
  return (
    <section className="min-w-0 rounded-md border border-line p-4" aria-label="Span detail">
      <h2 className="font-medium">{span.name}</h2>
      <p className="font-mono text-xs text-muted">{span.spanId}</p>
      {span.attributes["trayce.stubbed"] === "true" && (
        <p className="mt-1 text-xs text-muted" data-stubbed>
          Answered from the recording, not run.
        </p>
      )}
      {children}
      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="font-mono">{v}</dd>
          </div>
        ))}
      </dl>
      <Payload label="Input" preview={span.inputPreview} offloaded={span.inputOffloaded} href={payloadHref("input")} />
      <Payload label="Output" preview={span.outputPreview} offloaded={span.outputOffloaded} href={payloadHref("output")} />
      {Object.keys(span.attributes).length > 0 && (
        <>
          <h3 className="mt-4 text-xs font-medium text-muted">Attributes</h3>
          <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 font-mono text-xs">
            {Object.entries(span.attributes).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{k}</dt>
                <dd className="break-all">{v}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  );
}

function Payload({ label, preview, offloaded, href }: { label: string; preview: string; offloaded: boolean; href: string }) {
  return (
    <div className="mt-4" data-payload={label.toLowerCase()}>
      <h3 className="flex items-baseline gap-2 text-xs font-medium text-muted">
        {label}
        {offloaded && (
          <>
            <span>· preview of a large payload</span>
            <a href={href} className="text-foreground underline" target="_blank" rel="noreferrer">
              Full payload
            </a>
          </>
        )}
      </h3>
      <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-line/40 p-2 font-mono text-xs">
        {preview ? prettyPayload(preview) + (offloaded ? "\n…" : "") : "—"}
      </pre>
    </div>
  );
}
