import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { unstable_rethrow } from "next/navigation";
import { Crumbs } from "../datasets/ui";
import { projectLinks } from "@/lib/links";
import { formatCost, formatDuration } from "@/lib/format";
import { EMPTY, filterParams, parseFilter, type Filter } from "@/lib/filters";
import {
  analyticsQuery,
  isRange,
  RANGES,
  readAnalytics,
  viewerZone,
  type Group,
  type Range,
  type Window,
} from "@/lib/analytics";
import { Legend, Lines, StackedBars, Tile, type Column, type Series } from "./charts";
import { ViewerZone } from "./zone";

// The project's landing page (design.md, Phase 8): is the agent getting slower, pricier, or less
// reliable, and where? Every number links to the traces behind it.

const TOOL_SORTS = ["calls", "errors", "p95", "name"] as const;
type ToolSort = (typeof TOOL_SORTS)[number];

export default function OverviewPage(props: PageProps<"/p/[slug]/overview">) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Overview"]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Overview</h1>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Overview searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

/** What every panel needs: where it links, and the query it reads. */
type View = {
  at: (path: string) => string;
  range: Range;
  tz: string;
  filter: Filter;
  query: string;
  toolSort: ToolSort;
};

async function Overview({ searchParams }: { searchParams: PageProps<"/p/[slug]/overview">["searchParams"] }) {
  await connection(); // analytics change every request; never prerendered
  const [at, params, tz] = await Promise.all([projectLinks(), searchParams, viewerZone()]);
  const range = isRange(params.range) ? params.range : "24h";
  // The overview filters on tags, user, and service; the time comes from the range.
  const parsed = parseFilter(params);
  const all = params.all === "1";
  const filter: Filter = {
    ...EMPTY,
    tags: parsed.tags,
    user: parsed.user,
    service: parsed.service,
    evals: all,
    replays: all,
  };
  const sort = TOOL_SORTS.find((s) => s === params.tools) ?? "calls";
  const v: View = { at, range, tz, filter, query: analyticsQuery(range, tz, filter), toolSort: sort };
  return (
    <>
      <ViewerZone />
      <Controls v={v} all={all} />
      <Suspense fallback={<Placeholder h="h-24" />}>
        <Tiles v={v} />
      </Suspense>
      <div className="mt-8 grid gap-8 lg:grid-cols-2">
        <Section title="Volume and errors">
          <Suspense fallback={<Placeholder />}>
            <VolumePanel v={v} />
          </Suspense>
        </Section>
        <Section title="Trace latency">
          <Suspense fallback={<Placeholder />}>
            <LatencyPanel v={v} />
          </Suspense>
        </Section>
      </div>
      <Section title="Cost" className="mt-8">
        <Suspense fallback={<Placeholder />}>
          <CostPanel v={v} />
        </Suspense>
      </Section>
      <Section title="Tools" className="mt-8">
        <Suspense fallback={<Placeholder />}>
          <ToolsPanel v={v} />
        </Suspense>
      </Section>
      <p className="mt-10 text-sm text-muted">
        <Link href="/status" className="hover:underline">
          Service status
        </Link>
      </p>
    </>
  );
}

/** This page with its filter, range, and sort, changed by `over`. */
function overview(v: View, over: Record<string, string>): string {
  const q = filterParams(v.filter);
  q.delete("evals");
  q.delete("replays");
  if (v.filter.evals) q.set("all", "1");
  q.set("range", v.range);
  if (v.toolSort !== "calls") q.set("tools", v.toolSort);
  for (const [k, val] of Object.entries(over)) q.set(k, val);
  return v.at(`/overview?${q}`);
}

function Controls({ v, all }: { v: View; all: boolean }) {
  const here = (over: Record<string, string>) => overview(v, over);
  const input = "w-36 rounded border border-line bg-transparent px-2 py-1 text-sm";
  return (
    <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-3 text-sm">
      <nav aria-label="Range" className="flex gap-1">
        {RANGES.map((r) => (
          <Link
            key={r}
            href={here({ range: r })}
            aria-current={r === v.range ? "page" : undefined}
            className={`rounded px-2 py-1 font-mono text-xs ${r === v.range ? "bg-foreground text-background" : "border border-line hover:bg-line"}`}
          >
            {r}
          </Link>
        ))}
      </nav>
      <form className="flex flex-wrap items-end gap-2" aria-label="Filter">
        <input type="hidden" name="range" value={v.range} />
        <label className="flex flex-col text-xs text-muted">
          Tag
          <input name="tag" defaultValue={v.filter.tags[0] ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          User ID
          <input name="user" defaultValue={v.filter.user ?? ""} className={input} />
        </label>
        <label className="flex flex-col text-xs text-muted">
          Service
          <input name="service" defaultValue={v.filter.service ?? ""} className={input} />
        </label>
        <label className="flex items-center gap-1.5 py-1 text-xs">
          <input type="checkbox" name="all" value="1" defaultChecked={all} />
          Include eval and replay runs
        </label>
        <button className="rounded border border-line px-2 py-1 text-xs hover:bg-line">Apply</button>
      </form>
      <p className="ml-auto text-xs text-muted" data-zone={v.tz}>
        Approximate · times in {v.tz}
      </p>
    </div>
  );
}

function Section({ title, className = "", children }: { title: string; className?: string; children: React.ReactNode }) {
  return (
    <section className={className} aria-label={title}>
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="mt-2">{children}</div>
    </section>
  );
}

function Placeholder({ h = "h-48" }: { h?: string }) {
  return <div className={`${h} mt-6 animate-pulse rounded-md bg-line/40`} />;
}

function Failed({ error }: { error: unknown }) {
  return (
    <p className="rounded-md border border-line p-4 text-sm" data-panel-error>
      <span className="font-medium text-bad">Couldn&apos;t read analytics.</span>{" "}
      <span className="text-muted">{error instanceof Error ? error.message : String(error)}</span>
    </p>
  );
}

/** Reads a part, or renders why it couldn't. */
async function load<T>(read: () => Promise<T>): Promise<{ ok: true; data: T } | { ok: false; view: React.ReactNode }> {
  try {
    return { ok: true, data: await read() };
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return { ok: false, view: <Failed error={e} /> };
  }
}

// Links into the trace list.

function traces(v: View, over: Partial<Filter>): string {
  return v.at(`/traces?${filterParams({ ...v.filter, ...over })}`);
}

/** The bucket's own time range; the first starts where the window does, the last is open. */
function bucketRange(w: Window, i: number): Partial<Filter> {
  const since = i === 0 ? w.start : w.buckets[i];
  const until = w.buckets[i + 1];
  return { since, until };
}

function bucketLabel(w: Window, iso: string): string {
  const opts: Intl.DateTimeFormatOptions =
    w.bucket === "hour"
      ? { timeZone: w.tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }
      : { timeZone: w.tz, day: "numeric", month: "short" };
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(iso));
}

// Formatting.

const count = (n: number) => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const pct = (r: number | null) => (r === null ? "—" : `${(r * 100).toFixed(r < 0.1 && r > 0 ? 1 : 0)}%`);
const ms = (v: number | null) => (v === null ? "—" : formatDuration(v));
const usd = (v: number) => formatCost(v);

/** "+12% vs previous 24h"; None-safe. `points` compares rates in percentage points. */
function change(now: number | null, before: number | null | undefined, range: Range, points = false): string | undefined {
  if (before === undefined) return `no earlier ${range} within retention`;
  if (now === null || before === null) return undefined;
  if (points) {
    const d = (now - before) * 100;
    return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(1)} pts vs previous ${range}`;
  }
  if (before === 0) return now === 0 ? `same as previous ${range}` : `new vs previous ${range}`;
  const d = ((now - before) / before) * 100;
  return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(0)}% vs previous ${range}`;
}

// Panels.

async function Tiles({ v }: { v: View }) {
  const [vol, lat, cost] = await Promise.all([
    load(() => readAnalytics("volume", v.query)),
    load(() => readAnalytics("latency", v.query)),
    load(() => readAnalytics("cost", v.query)),
  ]);
  if (!vol.ok) return <div className="mt-6">{vol.view}</div>;
  const t = vol.data.totals;
  const prev = vol.data.previous;
  const w = vol.data.window;
  return (
    <div className="mt-6">
      {w.clamped && (
        <p className="mb-3 text-xs text-muted" data-clamped>
          This project keeps traces for {w.retention_days} days, so the range starts there.
        </p>
      )}
      {t.traces === 0 && (
        <p className="mb-3 text-sm text-muted" data-empty>
          No traces in the last {v.range}
          {v.filter.evals ? "" : " (eval and replay runs left out)"}.
        </p>
      )}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Traces"
          value={count(t.traces)}
          change={change(t.traces, prev ? prev.traces : undefined, v.range)}
          note={t.running ? `${t.running} running` : undefined}
          href={traces(v, { since: w.start })}
        />
        <Tile
          label="Error rate"
          value={pct(t.error_rate)}
          change={change(t.error_rate, prev ? prev.error_rate : undefined, v.range, true)}
          href={traces(v, { since: w.start, status: "error" })}
        />
        {lat.ok ? (
          <Tile
            label="p95 latency"
            value={ms(lat.data.totals.p95)}
            change={change(lat.data.totals.p95, lat.data.previous ? lat.data.previous.p95 : undefined, v.range)}
          />
        ) : (
          lat.view
        )}
        {cost.ok ? (
          <Tile
            label="Cost per trace"
            value={cost.data.totals.cost_per_trace === null ? "—" : formatCost(cost.data.totals.cost_per_trace, cost.data.totals.unpriced_llm_spans)}
            change={change(
              cost.data.totals.cost_per_trace,
              cost.data.previous ? cost.data.previous.cost_per_trace : undefined,
              v.range,
            )}
          />
        ) : (
          cost.view
        )}
      </div>
      {t.running > 0 && (
        <p className="mt-2 text-xs text-muted">
          {t.running} running {t.running === 1 ? "trace is" : "traces are"} counted in traces but not in error rate,
          latency, or cost until {t.running === 1 ? "it completes" : "they complete"}.
        </p>
      )}
    </div>
  );
}

async function VolumePanel({ v }: { v: View }) {
  const r = await load(() => readAnalytics("volume", v.query));
  if (!r.ok) return r.view;
  const { window: w, buckets, span_kinds } = r.data;
  const series: Series[] = [
    { key: "ok", label: "OK", color: "var(--series-1)" },
    { key: "error", label: "Error", color: "var(--bad)" },
    { key: "running", label: "Running", color: "var(--series-other)" },
  ];
  const columns: Column[] = buckets.map((b, i) => ({
    label: bucketLabel(w, b.start),
    href: traces(v, bucketRange(w, i)),
    values: { ok: b.completed - b.errors, error: b.errors, running: b.running },
  }));
  return (
    <>
      <StackedBars label="Traces per bucket by status" columns={columns} series={series} format={count} />
      <Legend series={series} />
      <table className="mt-4 w-full text-left text-sm" aria-label="Errors by span kind">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className="py-1 font-medium">Span kind</th>
            <th className="py-1 text-right font-medium">Spans</th>
            <th className="py-1 text-right font-medium">Errors</th>
            <th className="py-1 text-right font-medium">Error rate</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line font-mono text-xs">
          {span_kinds.map((k) => (
            <tr key={k.kind} data-kind={k.kind}>
              <td className="py-1">
                <Link href={traces(v, { since: w.start, kind: k.kind as Filter["kind"] })} className="hover:underline">
                  {k.kind}
                </Link>
              </td>
              <td className="py-1 text-right">{count(k.spans)}</td>
              <td className="py-1 text-right">
                {k.errors > 0 ? (
                  <Link
                    href={traces(v, { since: w.start, kind: k.kind as Filter["kind"], errors: true })}
                    className="text-bad hover:underline"
                  >
                    {count(k.errors)}
                  </Link>
                ) : (
                  0
                )}
              </td>
              <td className="py-1 text-right">{pct(k.error_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

async function LatencyPanel({ v }: { v: View }) {
  const [lat, tools, models] = await Promise.all([
    load(() => readAnalytics("latency", v.query)),
    load(() => readAnalytics("tools", v.query)),
    load(() => readAnalytics("models", v.query)),
  ]);
  if (!lat.ok) return lat.view;
  const { window: w, buckets } = lat.data;
  const series: Series[] = [
    { key: "p50", label: "p50", color: "var(--series-1)" },
    { key: "p95", label: "p95", color: "var(--series-2)" },
    { key: "p99", label: "p99", color: "var(--series-3)" },
  ];
  const columns: Column[] = buckets.map((b, i) => ({
    label: bucketLabel(w, b.start),
    href: traces(v, bucketRange(w, i)),
    values: { p50: b.p50, p95: b.p95, p99: b.p99 },
  }));
  return (
    <>
      <Lines label="Trace latency percentiles per bucket" columns={columns} series={series} format={(x) => formatDuration(x)} />
      <Legend series={series} />
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {tools.ok ? <Slowest title="Slowest tools" data={tools.data.groups} names={tools.data.slowest} link={(g) => traces(v, { since: w.start, kind: "tool", name: g.name })} min={tools.data.min_calls} /> : tools.view}
        {models.ok ? <Slowest title="Slowest models" data={models.data.groups} names={models.data.slowest} link={(g) => traces(v, { since: w.start, kind: "llm", model: g.name || undefined })} min={models.data.min_calls} /> : models.view}
      </div>
    </>
  );
}

function Slowest({
  title,
  data,
  names,
  link,
  min,
}: {
  title: string;
  data: Group[];
  names: string[];
  link: (g: Group) => string;
  min: number;
}) {
  const by = new Map(data.map((g) => [g.name, g]));
  return (
    <div aria-label={title}>
      <h3 className="text-xs font-medium text-muted">{title} (p95)</h3>
      {names.length === 0 ? (
        <p className="mt-1 text-xs text-muted">None with {min}+ calls yet.</p>
      ) : (
        <ol className="mt-1 divide-y divide-line font-mono text-xs">
          {names.map((n) => {
            const g = by.get(n)!;
            return (
              <li key={n} className="flex justify-between py-1" data-slowest={n}>
                <Link href={link(g)} className="truncate hover:underline">
                  {n || "(no model)"}
                </Link>
                <span>{ms(g.p95)}</span>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}

async function CostPanel({ v }: { v: View }) {
  const r = await load(() => readAnalytics("cost", v.query));
  if (!r.ok) return r.view;
  const { window: w, buckets, top_models, models, totals } = r.data;
  const colors = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)", "var(--series-5)"];
  // The top models take the palette's slots in order of spend in this window; the rest are
  // "Other". A model can change color when the range or filter changes its rank.
  const series: Series[] = [
    ...top_models.map((m, i) => ({ key: m, label: m || "(no model)", color: colors[i] })),
    { key: "\u0000other", label: "Other", color: "var(--series-other)" },
  ];
  const columns: Column[] = buckets.map((b, i) => ({
    label: bucketLabel(w, b.start),
    href: traces(v, { ...bucketRange(w, i), kind: "llm" }),
    values: { ...b.by_model, "\u0000other": b.other },
  }));
  const swatch = new Map(series.map((s) => [s.key, s.color]));
  const unpriced = models.filter((m) => m.unpriced_calls > 0);
  const spend = buckets.reduce((sum, b) => sum + b.total, 0);
  return (
    <>
      <p className="text-xs text-muted">
        {usd(spend)} on LLM calls in the last {v.range}, running traces included; {usd(totals.cost)} across{" "}
        {count(totals.completed)} completed traces.
      </p>
      <div className="mt-2 max-w-4xl">
        <StackedBars label="LLM spend per bucket by model" columns={columns} series={series} format={(x) => `$${x < 1 ? x.toFixed(x === 0 ? 0 : 3) : x.toFixed(2)}`} />
        <Legend series={series} />
      </div>
      <div className="mt-4 overflow-x-auto rounded-md border border-line">
        <table className="w-full text-left text-sm" aria-label="Cost by model">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Model</th>
              <th className="px-3 py-2 text-right font-medium">Calls</th>
              <th className="px-3 py-2 text-right font-medium">Cost</th>
              <th className="px-3 py-2 text-right font-medium">Per call</th>
              <th className="px-3 py-2 text-right font-medium">Input</th>
              <th className="px-3 py-2 text-right font-medium">Output</th>
              <th className="px-3 py-2 text-right font-medium">Cache read</th>
              <th className="px-3 py-2 text-right font-medium">Cache write</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line font-mono text-xs">
            {models.map((m) => (
              <tr key={m.model} data-model={m.model}>
                <td className="px-3 py-2">
                  <span className="flex items-center gap-1.5">
                    <span
                      className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm"
                      style={{ background: swatch.get(m.model) ?? "var(--series-other)" }}
                      aria-hidden
                    />
                    <Link href={traces(v, { since: w.start, kind: "llm", model: m.model || undefined })} className="hover:underline">
                      {m.model || "(no model)"}
                    </Link>
                  </span>
                </td>
                <td className="px-3 py-2 text-right">{count(m.calls)}</td>
                <td className="px-3 py-2 text-right">{m.calls > m.unpriced_calls ? usd(m.cost) : "—"}</td>
                <td className="px-3 py-2 text-right">{m.cost_per_call === null ? "—" : usd(m.cost_per_call)}</td>
                <td className="px-3 py-2 text-right">{count(m.input_tokens)}</td>
                <td className="px-3 py-2 text-right">{count(m.output_tokens)}</td>
                <td className="px-3 py-2 text-right">{count(m.cache_read_tokens)}</td>
                <td className="px-3 py-2 text-right">{count(m.cache_write_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {unpriced.length > 0 && (
        <ul className="mt-2 text-xs text-muted" aria-label="Unpriced calls" data-unpriced>
          {unpriced.map((m) => (
            <li key={m.model}>
              {count(m.unpriced_calls)} {m.unpriced_calls === 1 ? "call" : "calls"} to{" "}
              <span className="font-mono">{m.model || "(no model)"}</span>{" "}
              {m.unpriced_calls === 1 ? "has no price, so it isn't" : "have no price, so they aren't"} in the totals.
              Add the model to the price table (<span className="font-mono">model_prices</span>) to price new calls.
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

async function ToolsPanel({ v }: { v: View }) {
  const r = await load(() => readAnalytics("tools", v.query));
  if (!r.ok) return r.view;
  const { window: w, groups, min_calls } = r.data;
  if (groups.length === 0) return <p className="text-sm text-muted">No tool calls in the last {v.range}.</p>;
  const key: Record<ToolSort, (g: Group) => number | string> = {
    calls: (g) => -g.calls,
    errors: (g) => -(g.error_rate ?? 0),
    p95: (g) => -(g.p95 ?? -1),
    name: (g) => g.name,
  };
  const sorted = [...groups].sort((a, b) => {
    const [x, y] = [key[v.toolSort](a), key[v.toolSort](b)];
    return x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name);
  });
  const sortLink = (s: ToolSort) => overview(v, { tools: s });
  const head = (s: ToolSort, label: string, right = true) => (
    <th className={`px-3 py-2 font-medium ${right ? "text-right" : ""}`} aria-sort={v.toolSort === s ? "descending" : undefined}>
      <Link href={sortLink(s)} className={v.toolSort === s ? "text-foreground underline" : "hover:underline"}>
        {label}
      </Link>
    </th>
  );
  return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="w-full text-left text-sm" aria-label="Tools">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            {head("name", "Tool", false)}
            {head("calls", "Calls")}
            {head("errors", "Error rate")}
            <th className="px-3 py-2 text-right font-medium">p50</th>
            {head("p95", "p95")}
          </tr>
        </thead>
        <tbody className="divide-y divide-line font-mono text-xs">
          {sorted.map((g) => (
            <tr
              key={g.name}
              data-tool={g.name}
              className={g.ranked ? "" : "text-muted"}
              title={g.ranked ? undefined : `Fewer than ${min_calls} calls: not ranked`}
            >
              <td className="px-3 py-2">
                <Link href={traces(v, { since: w.start, kind: "tool", name: g.name })} className="hover:underline">
                  {g.name}
                </Link>
              </td>
              <td className="px-3 py-2 text-right">{count(g.calls)}</td>
              <td className="px-3 py-2 text-right">
                {g.errors > 0 ? (
                  <Link href={traces(v, { since: w.start, kind: "tool", name: g.name, errors: true })} className="text-bad hover:underline">
                    {pct(g.error_rate)}
                  </Link>
                ) : (
                  pct(g.error_rate)
                )}
              </td>
              <td className="px-3 py-2 text-right">{ms(g.p50)}</td>
              <td className="px-3 py-2 text-right">{ms(g.p95)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
