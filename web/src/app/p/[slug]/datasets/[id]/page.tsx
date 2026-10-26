import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset, getDraft, listVersions } from "@/lib/datasets";
import { liveTraces } from "@/lib/traces";
import { formatTime } from "@/lib/format";
import { setArchivedAction } from "../actions";
import { PublishForm, RenameForm } from "../forms";
import { ChangeBadge, Crumbs, SourceTrace, count, oneLine, trajectorySummary } from "../ui";
import { projectLinks } from "@/lib/links";

type Props = PageProps<"/p/[slug]/datasets/[id]">;

export default function DatasetPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Datasets", "/datasets"]]} />
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading dataset…</p>}>
        <DatasetView params={props.params} />
      </Suspense>
    </main>
  );
}

async function DatasetView({ params }: Pick<Props, "params">) {
  const at = await projectLinks();
  await connection();
  const { id } = await params;
  const [dataset, draft, versions] = await Promise.all([getDataset(id), getDraft(id), listVersions(id)]);
  if (!dataset || !draft) notFound();
  const live = await liveTraces(draft.items.flatMap((i) => (i.source_trace_id ? [i.source_trace_id] : [])));
  const unready = draft.items.filter((i) => i.needs_expectation).length;

  return (
    <>
      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <h1 className="font-mono text-2xl font-semibold tracking-tight">{dataset.name}</h1>
        {dataset.archived && <span className="font-mono text-xs text-muted">archived</span>}
      </div>
      {dataset.description && <p className="mt-1 text-sm text-muted">{dataset.description}</p>}

      {dataset.archived ? (
        <form action={setArchivedAction.bind(null, id, false)} className="mt-4">
          <p className="text-sm text-muted">Archived datasets are read-only and hidden from lists.</p>
          <button className="mt-2 rounded border border-line px-3 py-1 text-sm font-medium hover:bg-line/40">
            Unarchive
          </button>
        </form>
      ) : (
        <details className="mt-4 text-sm">
          <summary className="cursor-pointer text-muted">Rename or archive</summary>
          <div className="mt-3 space-y-3">
            <RenameForm dataset={id} name={dataset.name} description={dataset.description} />
            <form action={setArchivedAction.bind(null, id, true)}>
              <button className="rounded border border-line px-3 py-1 text-sm font-medium hover:bg-line/40">
                Archive dataset
              </button>
            </form>
          </div>
        </details>
      )}

      <section className="mt-8" aria-label="Draft">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="font-medium">Draft</h2>
          <span className="text-sm text-muted">
            {count(draft.items.length, "item")}
            {unready > 0 && (
              <span className="text-bad"> · {unready} need an expected output or trajectory before publishing</span>
            )}
          </span>
          {!dataset.archived && (
            <Link href={at(`/datasets/${id}/items/new`)} className="ml-auto text-sm font-medium underline">
              New item
            </Link>
          )}
        </div>
        {draft.items.length > 0 ? (
          <div className="mt-3 overflow-x-auto rounded-md border border-line">
            <table className="w-full text-left text-sm" aria-label="Draft items">
              <thead className="border-b border-line text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Input</th>
                  <th className="px-3 py-2 font-medium">Expected output</th>
                  <th className="px-3 py-2 font-medium">Expected trajectory</th>
                  <th className="px-3 py-2 font-medium">{draft.latest_version ? `Since v${draft.latest_version}` : "Status"}</th>
                  <th className="px-3 py-2 font-medium">Source trace</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {draft.items.map((item) => (
                  <tr key={item.item_id} data-item-id={item.item_id} data-needs-expectation={item.needs_expectation || undefined}>
                    <td className="max-w-xs break-all px-3 py-2 font-mono text-xs">
                      <Link href={at(`/datasets/${id}/items/${item.item_id}`)} className="hover:underline">
                        {oneLine(item.contents.input)}
                      </Link>
                    </td>
                    <td className="max-w-xs break-all px-3 py-2 font-mono text-xs">
                      {oneLine(item.contents.expected_output) || <Missing flagged={item.needs_expectation} />}
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      {trajectorySummary(item.contents) || <Missing flagged={item.needs_expectation} />}
                    </td>
                    <td className="px-3 py-2">
                      <ChangeBadge change={item.status} />
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">
                      <SourceTrace item={item} live={live} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted">
            Empty. Add items here, or open a trace and use <em>Add to dataset</em>.
          </p>
        )}
        {!dataset.archived && (
          <div className="mt-4">
            <PublishForm dataset={id} nextVersion={(draft.latest_version ?? 0) + 1} />
          </div>
        )}
      </section>

      <section className="mt-10" aria-label="Versions">
        <div className="flex flex-wrap items-baseline gap-3">
          <h2 className="font-medium">Published versions</h2>
          <Link href={at(`/experiments?dataset=${id}`)} className="ml-auto text-sm font-medium underline">
            Experiments on this dataset →
          </Link>
        </div>
        {versions.length === 0 ? (
          <p className="mt-2 text-sm text-muted">None yet. Evals run on published versions only.</p>
        ) : (
          <ul className="mt-2 divide-y divide-line rounded-md border border-line text-sm">
            {versions.map((v) => (
              <li key={v.version} className="flex gap-4 px-3 py-2" data-version={v.version}>
                <Link href={at(`/datasets/${id}/versions/${v.version}`)} className="font-mono font-medium hover:underline">
                  v{v.version}
                </Link>
                <span className="text-muted">{count(v.item_count, "item")}</span>
                <span className="ml-auto font-mono text-xs text-muted">{formatTime(new Date(v.published_at))}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function Missing({ flagged }: { flagged: boolean }) {
  return <span className={flagged ? "text-bad" : "text-muted"}>—</span>;
}
