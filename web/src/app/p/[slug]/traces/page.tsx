import { unstable_rethrow } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { clickHouseError, listTraces, type Cursor } from "@/lib/traces";
import { formatCost, formatDuration, formatTime } from "@/lib/format";
import { StatusBadge } from "./ui";
import { Crumbs } from "../datasets/ui";
import { projectLinks } from "@/lib/links";
import { describe, filterParams, KINDS, parseFilter, STATUSES, without, type Filter } from "@/lib/filters";
import { viewerZone } from "@/lib/analytics";

const PAGE_SIZE = 50;

export default function TracesPage(props: PageProps<"/p/[slug]/traces">) {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Traces"]]} />
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

async function TraceList({ searchParams }: { searchParams: PageProps<"/p/[slug]/traces">["searchParams"] }) {
  const at = await projectLinks();
  await connection(); // traces change every request; never prerendered
  const [params, zone] = await Promise.all([searchParams, viewerZone()]);
  const before = parseCursor(params.before);
  const filter = parseFilter(params);
  const list = (f: Filter, extra: Record<string, string> = {}) => {
    const q = filterParams(f);
    for (const [k, v] of Object.entries(extra)) q.set(k, v);
    return at(`/traces${q.size ? `?${q}` : ""}`);
  };
  const toggle = <Filters filter={filter} zone={zone} list={list} />;
  let page;
  try {
    page = await listTraces(PAGE_SIZE, before, filter);
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return (
      <p className="mt-6 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Couldn&apos;t read traces from ClickHouse.</span>{" "}
        <span className="text-muted" data-store-error>
          {clickHouseError(e)}
        </span>
      </p>
    );
  }
  const { traces, next } = page;

  if (traces.length === 0) {
    return (
      <>
        {toggle}
        <p className="mt-6 text-sm text-muted">
          {before
            ? "No older traces."
            : describe(filter).length
              ? "No traces match this filter."
              : "No traces yet. Run the demo agent with TRAYCE_API_KEY set."}
        </p>
      </>
    );
  }

  return (
    <>
      {toggle}
      <div className="mt-2 overflow-x-auto rounded-md border border-line">
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
                  <Link href={at(`/traces/${t.traceId}`)} className="font-medium hover:underline">
                    {t.rootName}
                  </Link>
                  {t.replayOf && (
                    <span className="ml-2 font-mono text-xs text-muted" data-replay-of={t.replayOf}>
                      replay
                    </span>
                  )}
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
          <Link href={list(filter, { before: `${next.start}_${next.traceId}` })} className="hover:underline">
            Older →
          </Link>
        </p>
      )}
    </>
  );
}

/** The active conditions as chips, each removable, the run toggles, and a form for the rest. */
function Filters({ filter, zone, list }: { filter: Filter; zone: string; list: (f: Filter) => string }) {
  const chips = describe(filter, zone).filter(([key]) => key !== "evals" && key !== "replays");
  const input = "w-32 rounded border border-line bg-transparent px-2 py-1 text-sm";
  return (
    <div className="mt-4 space-y-3 text-sm">
      {chips.length > 0 && (
        <ul className="flex flex-wrap gap-2" aria-label="Active filters">
          {chips.map(([key, label]) => (
            <li key={key} className="flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs" data-chip={key}>
              {label}
              <Link href={list(without(filter, key))} aria-label={`Remove: ${label}`} className="text-muted hover:text-foreground">
                ✕
              </Link>
            </li>
          ))}
          <li>
            <Link href={list({ ...filter, ...CLEARED })} className="text-xs text-muted hover:underline">
              Clear all
            </Link>
          </li>
        </ul>
      )}
      <form className="flex flex-wrap items-end gap-2" aria-label="Filter traces">
        {/* Conditions the form doesn't show ride along, so applying it doesn't drop them. */}
        {filter.since && <input type="hidden" name="since" value={filter.since} />}
        {filter.until && <input type="hidden" name="until" value={filter.until} />}
        {filter.tags.slice(1).map((t) => (
          <input key={t} type="hidden" name="tag" value={t} />
        ))}
        {filter.evals && <input type="hidden" name="evals" value="1" />}
        {filter.replays && <input type="hidden" name="replays" value="1" />}
        <label className="flex flex-col text-xs text-muted">
          Status
          <select name="status" defaultValue={filter.status ?? ""} className={input}>
            <option value="">any</option>
            {STATUSES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Tag
          <input name="tag" defaultValue={filter.tags[0] ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          User ID
          <input name="user" defaultValue={filter.user ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Service
          <input name="service" defaultValue={filter.service ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Span kind
          <select name="kind" defaultValue={filter.kind ?? ""} className={input}>
            <option value="">any</option>
            {KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-xs text-muted">
          Span name
          <input name="name" defaultValue={filter.name ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Model
          <input name="model" defaultValue={filter.model ?? ""} className={input} />
        </label>
        <label className="flex items-center gap-1.5 py-1 text-xs">
          <input type="checkbox" name="errors" value="1" defaultChecked={filter.errors} />
          errored spans only
        </label>
        <button className="rounded border border-line px-2 py-1 text-xs hover:bg-line">Apply</button>
      </form>
      <p className="flex gap-4">
        <Link href={list({ ...filter, replays: !filter.replays })} className="hover:underline" data-replays-toggle>
          {filter.replays ? "Hide replays" : "Show replays"}
        </Link>
        <Link href={list({ ...filter, evals: !filter.evals })} className="hover:underline" data-evals-toggle>
          {filter.evals ? "Hide eval runs" : "Show eval runs"}
        </Link>
      </p>
    </div>
  );
}

/** Every condition off except which runs are shown. */
const CLEARED: Partial<Filter> = {
  since: undefined,
  until: undefined,
  status: undefined,
  tags: [],
  user: undefined,
  service: undefined,
  kind: undefined,
  name: undefined,
  model: undefined,
  errors: false,
};
