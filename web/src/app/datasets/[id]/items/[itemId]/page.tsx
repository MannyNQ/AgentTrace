import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset, getDraft, type Contents } from "@/lib/datasets";
import { liveTraces } from "@/lib/traces";
import { editItemAction } from "../../../actions";
import { DeleteItem, ItemEditor, type ItemValues } from "../../../forms";
import { Crumbs } from "../../../ui";

type Props = PageProps<"/datasets/[id]/items/[itemId]">;

export default function ItemPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
        <EditItem params={props.params} />
      </Suspense>
    </main>
  );
}

function toValues(c: Contents): ItemValues {
  const show = (v: unknown) => (v === null || v === undefined ? "" : JSON.stringify(v, null, 2));
  return {
    input: show(c.input),
    expected_output: show(c.expected_output),
    expected_trajectory: show(c.expected_trajectory),
    metadata: Object.keys(c.metadata).length > 0 ? show(c.metadata) : "",
  };
}

async function EditItem({ params }: Pick<Props, "params">) {
  await connection();
  const { id, itemId } = await params;
  const [dataset, draft] = await Promise.all([getDataset(id), getDraft(id)]);
  const item = draft?.items.find((i) => i.item_id === itemId);
  if (!dataset || !item) notFound();
  const live = item.source_trace_id ? await liveTraces([item.source_trace_id]) : new Set<string>();

  return (
    <>
      <Crumbs trail={[["Datasets", "/datasets"], [dataset.name, `/datasets/${id}`]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Draft item</h1>
      <p className="mt-1 font-mono text-xs text-muted">
        {item.item_id} · rev {item.rev} · {item.status}
      </p>
      {item.source_trace_id && (
        <p className="mt-2 text-sm" data-source-trace>
          {!live || live.has(item.source_trace_id) ? (
            <>
              From trace{" "}
              <Link href={`/traces/${item.source_trace_id}`} className="font-mono underline">
                {item.source_trace_id}
              </Link>
              . Its steps are the failure: write down what should have happened.
            </>
          ) : (
            <span className="text-muted">
              Source trace expired ({item.source_trace_id}). The item keeps its copy of the input.
            </span>
          )}
        </p>
      )}
      <ItemEditor
        dataset={id}
        action={editItemAction.bind(null, id, item.item_id, item.rev)}
        submitLabel="Save item"
        initial={toValues(item.contents)}
      >
        <DeleteItem dataset={id} itemId={item.item_id} rev={item.rev} />
      </ItemEditor>
    </>
  );
}
