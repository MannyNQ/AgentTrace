import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { listTraces, type Cursor } from "@/lib/traces";
import { formatCost, formatDuration, formatTime } from "@/lib/format";
import { StatusBadge } from "./ui";

const PAGE_SIZE = 50;

export default function TracesPage(props: PageProps<"/traces">) {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <p className="font-mono text-xs uppercase tracking-widest text-muted">
        <Link href="/" className="hover:underline">
          AgentTrace
        </Link>{" "}
        / default project
      </p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Traces</h1>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading traces…</p>}>
        <TraceList searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

function parseCursor(before: string | string[] | undefined): Cursor | undefined {
  if (typeof before !== "string") return undefined;
  const [start, traceId] = before.split("_");
  return start && traceId ? { start, traceId } : undefined;
}

async function TraceList({ searchParams }: { searchParams: PageProps<"/traces">["searchParams"] }) {
  await connection(); // traces change every request; never prerendered
  const before = parseCursor((await searchParams).before);
  let page;
  try {
    page = await listTraces(PAGE_SIZE, before);
  } catch (e) {
    return (
      <p className="mt-6 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Couldn&apos;t read traces from ClickHouse.</span>{" "}
        <span className="text-muted">{e instanceof Error ? e.message : String(e)}</span>
      </p>
    );
  }
  const { traces, next } = page;

  if (traces.length === 0) {
    return (
      <p className="mt-6 text-sm text-muted">
        {before ? "No older traces." : "No traces yet. Run the demo agent with AGENTTRACE_API_KEY set."}
      </p>
    );
  }

  return (
    <>
      <div className="mt-6 overflow-x-auto rounded-md border border-line">
        <table className="w-full text-left text-sm" aria-label="Traces">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Started</th>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 text-right font-medium">Latency</th>
              <th className="px-3 py-2 text-right font-medium">Spans</th>
              <th className="px-3 py-2 text-right font-medium">Cost</th>
              <th className="px-3 py-2 font-medium">Tags</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {traces.map((t) => (
              <tr key={t.traceId} data-trace-id={t.traceId}>
                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                  {formatTime(t.startTime)}
                </td>
                <td className="px-3 py-2">
                  <Link href={`/traces/${t.traceId}`} className="font-medium hover:underline">
                    {t.rootName}
                  </Link>
                </td>
                <td className="px-3 py-2">
                  <StatusBadge status={t.status} />
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-xs">
                  {formatDuration(t.latencyMs)}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs">{t.spanCount}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right font-mono text-xs">
                  {formatCost(t.costUsd, t.unpricedLlmSpans)}
                </td>
                <td className="px-3 py-2 text-xs text-muted">{t.tags.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {next && (
        <p className="mt-4 text-sm">
          <Link href={`/traces?before=${next.start}_${next.traceId}`} className="hover:underline">
            Older →
          </Link>
        </p>
      )}
    </>
  );
}
