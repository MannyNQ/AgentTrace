"use client";

import Link from "next/link";
import { useActionState, useState, type ReactNode } from "react";
import {
  addTraceAction,
  createDatasetAction,
  deleteItemAction,
  publishAction,
  renameDatasetAction,
  type FormState,
} from "./actions";

type FormAction = (state: FormState, form: FormData) => Promise<FormState>;

const button =
  "rounded border border-line px-3 py-1 text-sm font-medium hover:bg-line/40 disabled:opacity-50";
const field = "w-full rounded border border-line bg-background px-2 py-1 font-mono text-sm";

function Notice({ state }: { state: FormState }) {
  return (
    <p aria-live="polite" className="text-sm" data-form-notice>
      {state.error && <span className="text-bad">{state.error}</span>}
      {state.message && <span className="text-good">{state.message}</span>}
      {state.link && (
        <>
          {" "}
          {/* A full load: the link can point back at this page, and a client navigation to the
              same URL would keep its stale form. */}
          <a href={state.link.href} className="underline">
            {state.link.label}
          </a>
        </>
      )}
    </p>
  );
}

function FieldError({ state, name }: { state: FormState; name: string }) {
  const error = state.fields?.[name];
  return error ? (
    <span className="block text-xs text-bad" data-field-error={name}>
      {error}
    </span>
  ) : null;
}

export function CreateDatasetForm() {
  const [state, action, pending] = useActionState(createDatasetAction, {});
  return (
    <form action={action} className="mt-4 flex flex-wrap items-end gap-3" aria-label="New dataset">
      <label className="text-xs text-muted">
        Name
        <input
          name="name"
          required
          placeholder="refund-failures"
          defaultValue={state.values?.name}
          className={`${field} mt-1 block w-56`}
        />
        <FieldError state={state} name="name" />
      </label>
      <label className="text-xs text-muted">
        Description
        <input
          name="description"
          defaultValue={state.values?.description}
          className={`${field} mt-1 block w-72 font-sans`}
        />
      </label>
      <button disabled={pending} className={button}>
        Create dataset
      </button>
      <Notice state={state} />
    </form>
  );
}

export function RenameForm({ dataset, name, description }: { dataset: string; name: string; description: string }) {
  const [state, action, pending] = useActionState(renameDatasetAction.bind(null, dataset), {});
  return (
    <form action={action} className="flex flex-wrap items-end gap-3" aria-label="Rename dataset">
      <label className="text-xs text-muted">
        Name
        <input name="name" required defaultValue={state.values?.name ?? name} className={`${field} mt-1 block w-56`} />
        <FieldError state={state} name="name" />
      </label>
      <label className="text-xs text-muted">
        Description
        <input
          name="description"
          defaultValue={state.values?.description ?? description}
          className={`${field} mt-1 block w-72 font-sans`}
        />
      </label>
      <button disabled={pending} className={button}>
        Save
      </button>
      <Notice state={state} />
    </form>
  );
}

const ITEM_FIELDS = [
  { key: "input", label: "Input", hint: "Required. What the agent is called with, as JSON.", rows: 6 },
  { key: "expected_output", label: "Expected output", hint: "Optional JSON.", rows: 4 },
  {
    key: "expected_trajectory",
    label: "Expected trajectory",
    hint: 'Optional. Tool calls the agent should make, e.g. [{"tool": "lookup_order", "args": {"order_id": "1042"}}]. Listed args must match; others are ignored.',
    rows: 5,
  },
  { key: "metadata", label: "Metadata", hint: 'Optional object of strings, e.g. {"source": "support"}', rows: 2 },
] as const;

export type ItemValues = Record<(typeof ITEM_FIELDS)[number]["key"], string>;

/** The JSON item form. `action` creates or edits; `children` holds anything below it, such as
 * the edit page's delete form. */
export function ItemEditor({
  dataset,
  action: save,
  submitLabel,
  initial,
  children,
}: {
  dataset: string;
  action: FormAction;
  submitLabel: string;
  initial: ItemValues;
  children?: ReactNode;
}) {
  const [state, action, pending] = useActionState(save, {});
  return (
    <>
      <form action={action} className="mt-6 space-y-4" aria-label="Item">
        {ITEM_FIELDS.map(({ key, label, hint, rows }) => (
          <label key={key} className="block">
            <span className="text-sm font-medium">{label}</span>
            <span className="ml-2 text-xs text-muted">{hint}</span>
            <textarea
              name={key}
              rows={rows}
              defaultValue={state.values?.[key] ?? initial[key]}
              aria-invalid={state.fields?.[key] ? true : undefined}
              className={`${field} mt-1 block`}
            />
            <FieldError state={state} name={key} />
          </label>
        ))}
        <div className="flex flex-wrap items-center gap-3">
          <button disabled={pending} className={button}>
            {submitLabel}
          </button>
          <Link href={`/datasets/${dataset}`} className="text-sm text-muted hover:underline">
            Cancel
          </Link>
          <Notice state={state} />
        </div>
      </form>
      {children}
    </>
  );
}

export function DeleteItem({ dataset, itemId, rev }: { dataset: string; itemId: string; rev: number }) {
  const [state, action, pending] = useActionState(deleteItemAction.bind(null, dataset, itemId, rev), {});
  return (
    <form action={action} className="mt-8 border-t border-line pt-4">
      <p className="text-xs text-muted">Drops the item from the next version. Published versions keep it.</p>
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button disabled={pending} className={`${button} text-bad`}>
          Delete from draft
        </button>
        <Notice state={state} />
      </div>
    </form>
  );
}

export function PublishForm({ dataset, nextVersion }: { dataset: string; nextVersion: number }) {
  const [state, action, pending] = useActionState(publishAction.bind(null, dataset), {});
  return (
    <form action={action} className="flex flex-wrap items-center gap-3" aria-label="Publish">
      <button disabled={pending} className={button}>
        Publish v{nextVersion}
      </button>
      <Notice state={state} />
    </form>
  );
}

export function AddToDatasetForm({ traceId, datasets }: { traceId: string; datasets: { id: string; name: string }[] }) {
  const [state, action, pending] = useActionState(addTraceAction.bind(null, traceId), {});
  const [choice, setChoice] = useState(datasets[0]?.id ?? ""); // "": a new dataset
  return (
    <form action={action} className="flex flex-wrap items-center gap-2" aria-label="Add to dataset">
      <select
        name="dataset"
        aria-label="Dataset"
        className="w-auto max-w-xs rounded border border-line bg-background px-2 py-1 font-mono text-sm"
        value={choice}
        onChange={(e) => setChoice(e.target.value)}
      >
        {datasets.map((d) => (
          <option key={d.id} value={d.id}>
            {d.name}
          </option>
        ))}
        <option value="">New dataset…</option>
      </select>
      {choice === "" && (
        <input
          name="new_name"
          aria-label="New dataset name"
          required
          placeholder="refund-failures"
          defaultValue={state.values?.new_name}
          className="w-48 rounded border border-line bg-background px-2 py-1 font-mono text-sm"
        />
      )}
      <button disabled={pending} className={button}>
        Add to dataset
      </button>
      <Notice state={state} />
      <FieldError state={state} name="name" />
    </form>
  );
}
