import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { flattenTree, getTrace, type SpanRow, type TraceSummary } from "@/lib/traces";
import { formatCost, formatDuration, formatTime, prettyPayload } from "@/lib/format";
import { KindBadge, StatusBadge } from "../ui";

type Props = PageProps<"/traces/[traceId]">;

export default function TracePage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <p className="font-mono text-xs uppercase tracking-widest text-muted">
        <Link href="/traces" className="hover:underline">
          Traces
        </Link>
      </p>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading trace…</p>}>
        <TraceView params={props.params} searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

async function TraceView({ params, searchParams }: Pick<Props, "params" | "searchParams">) {
  await connection();
  const { traceId } = await params;
  const { span: selectedId } = await searchParams;
  const trace = await getTrace(traceId);
  if (!trace) notFound();

  const { summary, spans } = trace;
  const nodes = flattenTree(spans);
  const selected = spans.find((s) => s.spanId === selectedId) ?? nodes[0]?.span;

  return (
    <>
      <Header summary={summary} />
      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ol className="divide-y divide-line rounded-md border border-line" aria-label="Span tree">
          {nodes.map(({ span, depth }) => (
            <li key={span.spanId} data-span-id={span.spanId} data-kind={span.kind}>
              <Link
                href={`/traces/${traceId}?span=${span.spanId}`}
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
        {selected && <SpanDetail span={selected} traceId={traceId} />}
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

function SpanDetail({ span, traceId }: { span: SpanRow; traceId: string }) {
  const facts: [string, string][] = [
    ["Kind", span.kind],
    ["Status", span.status + (span.errorType ? ` (${span.errorType})` : "")],
    ["Duration", span.endTime ? formatDuration(span.endTime.getTime() - span.startTime.getTime()) : "—"],
  ];
  if (span.kind === "llm") {
    facts.push(
      ["Model", span.model || "—"],
      ["Tokens", `${span.inputTokens ?? "?"} in · ${span.outputTokens ?? "?"} out`],
      ["Cost", formatCost(span.costUsd)],
    );
  }
  return (
    <section className="min-w-0 rounded-md border border-line p-4" aria-label="Span detail">
      <h2 className="font-medium">{span.name}</h2>
      <p className="font-mono text-xs text-muted">{span.spanId}</p>
      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        {facts.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="font-mono">{v}</dd>
          </div>
        ))}
      </dl>
      <Payload label="Input" preview={span.inputPreview} uri={span.inputUri} href={payloadHref(traceId, span, "input")} />
      <Payload label="Output" preview={span.outputPreview} uri={span.outputUri} href={payloadHref(traceId, span, "output")} />
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

function payloadHref(traceId: string, span: SpanRow, which: "input" | "output") {
  return `/api/traces/${traceId}/spans/${span.spanId}/${which}`;
}

function Payload({ label, preview, uri, href }: { label: string; preview: string; uri: string; href: string }) {
  return (
    <div className="mt-4" data-payload={label.toLowerCase()}>
      <h3 className="flex items-baseline gap-2 text-xs font-medium text-muted">
        {label}
        {uri && (
          <>
            <span>· preview of a large payload</span>
            <a href={href} className="text-foreground underline" target="_blank" rel="noreferrer">
              Full payload
            </a>
          </>
        )}
      </h3>
      <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-line/40 p-2 font-mono text-xs">
        {preview ? prettyPayload(preview) + (uri ? "\n…" : "") : "—"}
      </pre>
    </div>
  );
}
