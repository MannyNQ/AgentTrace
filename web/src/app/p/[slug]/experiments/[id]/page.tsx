import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset, getVersion } from "@/lib/datasets";
import {
  getComparison,
  getExperiment,
  listExperiments,
  type ExperimentDetail,
  type ScorerSpec,
} from "@/lib/experiments";
import { formatCost, formatDuration, formatTime } from "@/lib/format";
import { Crumbs, count, oneLine } from "../../datasets/ui";
import { BaselineLine, CompareNotes, ScorerComparisonTable, Verdict } from "../compare";
import { GitRef, PassCount, StatusBadge, experimentLabel, pct } from "../ui";
import { projectLinks } from "@/lib/links";

type Props = PageProps<"/p/[slug]/experiments/[id]">;

export default function ExperimentPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Experiments", "/experiments"]]} />
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading experiment…</p>}>
        <ExperimentView params={props.params} />
      </Suspense>
    </main>
  );
}

/** What a scorer checks, from its eval.yaml config, in a few words. */
function describe(s: ScorerSpec): string {
  const c = s.config;
  if (s.type === "trajectory") {
    if (c.mode === "forbidden") return `trajectory · never calls ${(c.tools as string[]).join(", ")}`;
    if (c.mode === "max_steps") return `trajectory · at most ${c.max} tool calls`;
    return `trajectory · ${String(c.mode).replace("_", " ")}`;
  }
  if (s.type === "regex" && c.pattern) return `regex · /${c.pattern}/`;
  if (s.type === "custom") return `custom · ${c.function}`;
  if (s.type === "llm_judge") return `llm judge · ${c.model} · ${String(c.criteria)}`;
  return s.type.replace("_", " ");
}

async function ExperimentView({ params }: Pick<Props, "params">) {
  const at = await projectLinks();
  await connection();
  const { id } = await params;
  const e = await getExperiment(id);
  if (!e) notFound();
  const [dataset, version] = await Promise.all([
    getDataset(e.dataset_id),
    getVersion(e.dataset_id, e.dataset_version),
  ]);
  const inputs = new Map(version?.items.map((i) => [i.item_id, i.contents.input]) ?? []);
  const expected = e.item_count * e.samples;
  const elapsed = e.finished_at ? Date.parse(e.finished_at) - Date.parse(e.started_at) : null;

  return (
    <>
      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{experimentLabel(e)}</h1>
        <StatusBadge status={e.status} />
      </div>
      <p className="mt-1 text-sm text-muted">
        <Link href={at(`/datasets/${e.dataset_id}/versions/${e.dataset_version}`)} className="font-mono underline">
          {dataset?.name ?? e.dataset_id.slice(0, 8)} v{e.dataset_version}
        </Link>{" "}
        · {count(e.item_count, "item")} × {count(e.samples, "sample")} · {e.samples_uploaded}/{expected}{" "}
        uploaded · cost{" "}
        {e.samples_uploaded === 0 ? "—" : formatCost(e.cost_usd, e.unpriced_samples)}
        {e.unpriced_samples > 0 && ` (${count(e.unpriced_samples, "sample")} unpriced)`}
        {e.judge_cost_usd > 0 && ` · judge ${formatCost(e.judge_cost_usd)}`}
      </p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
        <dt className="text-muted">Started</dt>
        <dd className="font-mono">
          {formatTime(new Date(e.started_at))}
          {elapsed !== null && <span className="text-muted"> · took {formatDuration(elapsed)}</span>}
        </dd>
        <dt className="text-muted">Git</dt>
        <dd>
          <GitRef e={e} />
        </dd>
        <dt className="text-muted">Entry</dt>
        <dd className="font-mono">{String(e.config.entry ?? "—")}</dd>
        <dt className="text-muted">eval.yaml hash</dt>
        <dd className="font-mono" title={e.config_hash}>
          {e.config_hash.slice(0, 12)}
        </dd>
        <dt className="text-muted">ID</dt>
        <dd className="font-mono">{e.id}</dd>
      </dl>
      {e.status === "failed" && (
        <p className="mt-4 rounded-md border border-line p-3 text-sm" data-run-error>
          <span className="font-medium text-bad">The run stopped.</span>{" "}
          <span className="font-mono text-xs text-muted">{e.error ?? "no reason recorded"}</span>
        </p>
      )}
      {e.status === "running" && (
        <p className="mt-4 text-sm text-muted">
          Still running, or the runner stopped without finishing it. Reload for progress.
        </p>
      )}

      {e.status === "completed" && (
        <Suspense fallback={<p className="mt-8 text-sm text-muted">Comparing with the baseline…</p>}>
          <BaselinePanel e={e} />
        </Suspense>
      )}
      <ScorerTable e={e} />
      <ItemTable e={e} inputs={inputs} />
    </>
  );
}

/** ADR-0007: the baseline ingest selects for this run, and other runs it can be compared with:
 * completed, with the same eval.yaml hash. */
async function BaselinePanel({ e }: { e: ExperimentDetail }) {
  const at = await projectLinks();
  const [r, all] = await Promise.all([getComparison(e.id), listExperiments()]);
  const others = all.filter((x) => x.id !== e.id && x.status === "completed" && x.config_hash === e.config_hash);
  return (
    <section className="mt-8" aria-label="Versus baseline">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-medium">Versus baseline</h2>
        {r.ok && (
          <span className="text-sm">
            <Verdict c={r.data} /> <BaselineLine c={r.data} />
          </span>
        )}
        <details className="relative ml-auto text-sm" data-compare-picker>
          <summary className="cursor-pointer text-muted hover:underline">Compare with…</summary>
          <ul className="absolute right-0 z-10 mt-1 max-h-72 w-80 overflow-auto rounded-md border border-line bg-background p-1 shadow">
            {others.length === 0 && (
              <li className="p-2 text-xs text-muted">No other completed run with this eval.yaml hash.</li>
            )}
            {others.map((x) => (
              <li key={x.id}>
                <Link
                  href={at(`/experiments/${e.id}/compare/${x.id}`)}
                  className="block rounded px-2 py-1 text-xs hover:bg-line/40"
                  data-compare-with={x.id}
                >
                  <span className="font-mono">{experimentLabel(x)}</span>{" "}
                  <span className="text-muted">
                    <GitRef e={x} /> · {formatTime(new Date(x.started_at))}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </details>
      </div>
      {!r.ok ? (
        <p className="mt-2 text-sm text-bad">{r.error.error}</p>
      ) : !r.data.baseline ? (
        <p className="mt-2 text-sm text-muted" data-no-baseline>
          No baseline yet: no completed run with this eval.yaml hash at the merge-base or on the base
          branch{e.git_base_branch ? ` (${e.git_base_branch})` : ""}. The CI gate passes without one.
        </p>
      ) : (
        <>
          <ScorerComparisonTable c={r.data} />
          <CompareNotes c={r.data} />
          <p className="mt-2 text-sm">
            <Link
              href={at(`/experiments/${e.id}/compare/${r.data.baseline.id}`)}
              className="underline"
              data-open-comparison
            >
              Open the comparison
              {r.data.item_regressions.length > 0 &&
                ` (${count(r.data.item_regressions.length, "item regression")})`}{" "}
              →
            </Link>
          </p>
        </>
      )}
    </section>
  );
}

function ScorerTable({ e }: { e: ExperimentDetail }) {
  const stats = new Map(e.scorer_stats.map((s) => [s.scorer, s]));
  return (
    <section className="mt-8" aria-label="Scorers">
      <h2 className="font-medium">Scorers</h2>
      <p className="mt-1 text-xs text-muted">
        Pass rate over graded samples; items without a scorer&apos;s expectation aren&apos;t graded by it.
        &ldquo;By sample&rdquo; is each repeat&apos;s pass rate, the spread between runs.
      </p>
      <div className="mt-3 overflow-x-auto rounded-md border border-line">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Scorer</th>
              <th className="px-3 py-2 font-medium">Checks</th>
              <th className="px-3 py-2 text-right font-medium">Pass rate</th>
              <th className="px-3 py-2 text-right font-medium">Passed</th>
              <th className="px-3 py-2 text-right font-medium">Mean score</th>
              <th className="px-3 py-2 font-medium">By sample</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {e.scorers.map((spec) => {
              const s = stats.get(spec.name);
              return (
                <tr key={spec.name} data-scorer={spec.name}>
                  <td className="px-3 py-2 font-mono text-xs font-medium">{spec.name}</td>
                  <td className="px-3 py-2 text-xs text-muted">
                    {describe(spec)}
                    {spec.threshold < 1 && ` · passes at ≥ ${spec.threshold}`}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs" data-pass-rate>
                    {pct(s?.pass_rate ?? null)}
                  </td>
                  <td className="px-3 py-2 text-right text-xs">
                    <PassCount passed={s?.passed ?? 0} scored={s?.scored ?? 0} />
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {s?.mean_score == null ? "—" : s.mean_score.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-muted">
                    {s?.pass_rate_by_sample.map(pct).join(" · ") ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

async function ItemTable({ e, inputs }: { e: ExperimentDetail; inputs: Map<string, unknown> }) {
  const at = await projectLinks();
  return (
    <section className="mt-8" aria-label="Items">
      <h2 className="font-medium">Items</h2>
      <p className="mt-1 text-xs text-muted">
        Samples passed per scorer. Open an item for each sample&apos;s output, tool calls, and reasons.
      </p>
      <div className="mt-3 overflow-x-auto rounded-md border border-line">
        <table className="w-full text-left text-sm" aria-label="Experiment items">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Input</th>
              {e.scorers.map((s) => (
                <th key={s.name} className="px-3 py-2 text-right font-mono font-medium">
                  {s.name}
                </th>
              ))}
              <th className="px-3 py-2 text-right font-medium">Errored</th>
              <th className="px-3 py-2 text-right font-medium">Latency</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {e.items.map((item) => (
              <tr key={item.item_id} data-item-id={item.item_id}>
                <td className="max-w-md px-3 py-2">
                  <Link
                    href={at(`/experiments/${e.id}/items/${item.item_id}`)}
                    className="block truncate font-mono text-xs hover:underline"
                  >
                    {oneLine(inputs.get(item.item_id)) || item.item_id}
                  </Link>
                </td>
                {e.scorers.map((s) => {
                  const c = item.scores[s.name];
                  return (
                    <td key={s.name} className="px-3 py-2 text-right text-xs" data-scorer={s.name}>
                      {c ? <PassCount passed={c.passed} scored={c.scored} /> : <span className="text-muted">n/a</span>}
                    </td>
                  );
                })}
                <td className={`px-3 py-2 text-right font-mono text-xs ${item.errored ? "text-bad" : "text-muted"}`}>
                  {item.errored}
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-muted">
                  {item.mean_latency_ms === null ? "—" : formatDuration(item.mean_latency_ms)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
