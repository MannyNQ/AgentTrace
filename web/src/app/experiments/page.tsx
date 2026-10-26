import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { listDatasets } from "@/lib/datasets";
import { listExperiments } from "@/lib/experiments";
import { formatCost, formatTime } from "@/lib/format";
import { Crumbs } from "../datasets/ui";
import { GitRef, StatusBadge, experimentLabel } from "./ui";

type Props = PageProps<"/experiments">;

export default function ExperimentsPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["AgentTrace", "/"], ["default project"]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Experiments</h1>
      <p className="mt-1 text-sm text-muted">
        Eval runs uploaded by <code className="font-mono">agenttrace eval</code>: one version of your
        agent over one dataset version, newest first.
      </p>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading experiments…</p>}>
        <ExperimentList searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

async function ExperimentList({ searchParams }: Pick<Props, "searchParams">) {
  await connection();
  const raw = (await searchParams).dataset;
  const dataset = typeof raw === "string" && raw ? raw : undefined;
  let datasets, experiments;
  try {
    [datasets, experiments] = await Promise.all([listDatasets(true), listExperiments(dataset)]);
  } catch (e) {
    return (
      <p className="mt-6 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Couldn&apos;t read experiments from the ingest API.</span>{" "}
        <span className="text-muted">{e instanceof Error ? e.message : String(e)}</span>
      </p>
    );
  }
  const names = new Map(datasets.map((d) => [d.id, d.name]));

  return (
    <>
      {/* A plain GET form, so the filter is a link that works without JavaScript. */}
      <form method="get" className="mt-6 flex flex-wrap items-center gap-2 text-sm">
        <label htmlFor="dataset" className="text-muted">
          Dataset
        </label>
        <select
          id="dataset"
          name="dataset"
          defaultValue={dataset ?? ""}
          className="rounded border border-line bg-background px-2 py-1 font-mono text-xs"
        >
          <option value="">All datasets</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
              {d.archived ? " (archived)" : ""}
            </option>
          ))}
        </select>
        <button className="rounded border border-line px-3 py-1 font-medium hover:bg-line/40">Filter</button>
        {dataset && (
          <Link href="/experiments" className="text-muted underline">
            Clear
          </Link>
        )}
      </form>

      {experiments.length === 0 ? (
        <p className="mt-4 text-sm text-muted">
          {dataset ? "No experiments on this dataset yet." : "No experiments yet."} Run{" "}
          <code className="font-mono">agenttrace eval</code> with an eval.yaml to create one.
        </p>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-md border border-line">
          <table className="w-full text-left text-sm" aria-label="Experiments">
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                <th className="px-3 py-2 font-medium">Experiment</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium">Dataset</th>
                <th className="px-3 py-2 text-right font-medium">Samples</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
                <th className="px-3 py-2 font-medium">Git</th>
                <th className="px-3 py-2 font-medium">Started</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {experiments.map((e) => (
                <tr key={e.id} data-experiment={e.id}>
                  <td className="px-3 py-2">
                    <Link href={`/experiments/${e.id}`} className="font-medium hover:underline">
                      {experimentLabel(e)}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <StatusBadge status={e.status} />
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">
                    <Link href={`/datasets/${e.dataset_id}/versions/${e.dataset_version}`} className="hover:underline">
                      {names.get(e.dataset_id) ?? e.dataset_id.slice(0, 8)} v{e.dataset_version}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {e.samples_uploaded}/{e.item_count * e.samples}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {e.samples_uploaded === 0 ? "—" : formatCost(e.cost_usd, e.unpriced_samples)}
                  </td>
                  <td className="px-3 py-2">
                    <GitRef e={e} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                    {formatTime(new Date(e.started_at))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
