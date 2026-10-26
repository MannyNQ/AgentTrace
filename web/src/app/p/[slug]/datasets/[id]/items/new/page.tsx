import { notFound } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { getDataset } from "@/lib/datasets";
import { createItemAction } from "../../../actions";
import { ItemEditor } from "../../../forms";
import { Crumbs } from "../../../ui";

type Props = PageProps<"/p/[slug]/datasets/[id]/items/new">;

export default function NewItemPage(props: Props) {
  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
        <NewItem params={props.params} />
      </Suspense>
    </main>
  );
}

async function NewItem({ params }: Pick<Props, "params">) {
  await connection();
  const { id } = await params;
  const dataset = await getDataset(id);
  if (!dataset) notFound();
  return (
    <>
      <Crumbs trail={[["Datasets", "/datasets"], [dataset.name, `/datasets/${id}`]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">New draft item</h1>
      <ItemEditor
        dataset={id}
        action={createItemAction.bind(null, id)}
        submitLabel="Add item"
        initial={{ input: "", expected_output: "", expected_trajectory: "", metadata: "" }}
      />
    </>
  );
}
