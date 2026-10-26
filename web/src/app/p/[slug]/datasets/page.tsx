import { unstable_rethrow } from "next/navigation";
import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { listDatasets } from "@/lib/datasets";
import { formatTime } from "@/lib/format";
import { CreateDatasetForm } from "./forms";
import { Crumbs } from "./ui";
import { projectLinks } from "@/lib/links";

export default function DatasetsPage(props: PageProps<"/p/[slug]/datasets">) {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Datasets"]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Datasets</h1>
      <p className="mt-1 text-sm text-muted">
        Test cases for your agent. Add failed traces to a draft, write down what should have
        happened, and publish a version for evals to run.
      </p>
      <CreateDatasetForm />
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading datasets…</p>}>
        <DatasetList searchParams={props.searchParams} />
      </Suspense>
    </main>
  );
}

/** Archived datasets are hidden unless `?archived=1`, which is the way back to unarchiving one. */
async function DatasetList({ searchParams }: { searchParams: PageProps<"/p/[slug]/datasets">["searchParams"] }) {
  const at = await projectLinks();
  await connection();
  const showArchived = (await searchParams).archived === "1";
  const toggle = (
    <p className="mt-6 text-sm">
      <Link href={showArchived ? at("/datasets") : at("/datasets?archived=1")} className="text-muted underline">
        {showArchived ? "Hide archived" : "Show archived"}
      </Link>
    </p>
  );
  let datasets;
  try {
    datasets = await listDatasets(showArchived);
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    return (
      <p className="mt-6 rounded-md border border-line p-4 text-sm">
        <span className="font-medium text-bad">Couldn&apos;t read datasets from the ingest API.</span>{" "}
        <span className="text-muted">{e instanceof Error ? e.message : String(e)}</span>
      </p>
    );
  }
  if (datasets.length === 0) {
    return (
      <>
        {toggle}
        <p className="mt-2 text-sm text-muted">No datasets yet.</p>
      </>
    );
  }
  return (
    <>
      {toggle}
      <div className="mt-2 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-left text-sm" aria-label="Datasets">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className="px-3 py-2 font-medium">Name</th>
            <th className="px-3 py-2 font-medium">Description</th>
            <th className="px-3 py-2 text-right font-medium">Draft items</th>
            <th className="px-3 py-2 font-medium">Latest version</th>
            <th className="px-3 py-2 font-medium">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {datasets.map((d) => (
            <tr key={d.id} data-dataset={d.name}>
              <td className="px-3 py-2">
                <Link href={at(`/datasets/${d.id}`)} className="font-mono font-medium hover:underline">
                  {d.name}
                </Link>
                {d.archived && <span className="ml-2 font-mono text-xs text-muted">archived</span>}
              </td>
              <td className="px-3 py-2 text-muted">{d.description}</td>
              <td className="px-3 py-2 text-right font-mono text-xs">{d.draft_items}</td>
              <td className="px-3 py-2 font-mono text-xs">
                {d.latest_version ? `v${d.latest_version}` : "unpublished"}
              </td>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-muted">
                {formatTime(new Date(d.created_at))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}
