"use server";

import { redirect } from "next/navigation";
import { refresh } from "next/cache";
import * as api from "@/lib/datasets";
import type { ApiError, Contents } from "@/lib/datasets";
import { projectLinks } from "@/lib/links";

// The rules live behind the ingest API (ADR-0019); these actions only translate forms to calls.

export type FormState = {
  error?: string;
  message?: string;
  fields?: Record<string, string>;
  /** A link that helps with the outcome, such as the existing item for a duplicate trace. */
  link?: { href: string; label: string };
  // React resets a form after its action runs; a refused form shows what was typed again.
  values?: Record<string, string>;
};

function text(form: FormData, key: string): string {
  const v = form.get(key);
  return typeof v === "string" ? v : "";
}

/** A refused form keeps its values. A taken name links to its holder, which may be archived
 * and so missing from the list. */
async function refusal(e: ApiError, values?: Record<string, string>): Promise<FormState> {
  const at = await projectLinks();
  const link = e.dataset_id ? { href: at(`/datasets/${e.dataset_id}`), label: "Open that dataset" } : undefined;
  return { error: e.error, fields: e.fields, values, link };
}

export async function createDatasetAction(_: FormState, form: FormData): Promise<FormState> {
  const at = await projectLinks();
  const values = { name: text(form, "name"), description: text(form, "description") };
  const r = await api.createDataset(values.name.trim(), values.description);
  if (!r.ok) return refusal(r.error, values);
  redirect(at(`/datasets/${r.data.id}`));
}

export async function renameDatasetAction(dataset: string, _: FormState, form: FormData): Promise<FormState> {
  const values = { name: text(form, "name"), description: text(form, "description") };
  const r = await api.patchDataset(dataset, { name: values.name.trim(), description: values.description });
  if (!r.ok) return refusal(r.error, values);
  refresh();
  return { message: "Saved" };
}

export async function setArchivedAction(dataset: string, archived: boolean): Promise<void> {
  const at = await projectLinks();
  const r = await api.patchDataset(dataset, { archived });
  if (!r.ok) throw new Error(r.error.error);
  redirect(archived ? at("/datasets") : at(`/datasets/${dataset}`));
}

const ITEM_FIELDS = ["input", "expected_output", "expected_trajectory", "metadata"] as const;

/** Each textarea holds JSON; empty means "none". The API validates the shapes. */
function parseItem(form: FormData): { contents: Partial<Contents> } | { fields: Record<string, string> } {
  const contents: Record<string, unknown> = {};
  const fields: Record<string, string> = {};
  for (const key of ITEM_FIELDS) {
    const raw = text(form, key).trim();
    if (raw === "") continue;
    try {
      contents[key] = JSON.parse(raw);
    } catch (e) {
      fields[key] = `not valid JSON: ${e instanceof Error ? e.message : e}`;
    }
  }
  if (!("input" in contents) && !fields.input) fields.input = "required";
  return Object.keys(fields).length > 0 ? { fields } : { contents };
}

async function saveItem(
  form: FormData,
  save: (contents: Partial<Contents>) => Promise<api.Result<api.Item>>,
  onRefusal: (e: ApiError) => FormState["link"],
  dataset: string,
): Promise<FormState> {
  const at = await projectLinks();
  const values = Object.fromEntries(ITEM_FIELDS.map((k) => [k, text(form, k)]));
  const parsed = parseItem(form);
  if ("fields" in parsed) return { fields: parsed.fields, values };
  const r = await save(parsed.contents);
  if (!r.ok) return { ...(await refusal(r.error, values)), link: onRefusal(r.error) };
  redirect(at(`/datasets/${dataset}`));
}

export async function createItemAction(dataset: string, _: FormState, form: FormData): Promise<FormState> {
  return saveItem(form, (c) => api.addItem(dataset, c), () => undefined, dataset);
}

/** Adds a revision based on `rev`; a stale `rev` offers to reload the item. */
export async function editItemAction(
  dataset: string,
  itemId: string,
  rev: number,
  _: FormState,
  form: FormData,
): Promise<FormState> {
  const at = await projectLinks();
  const reload = { href: at(`/datasets/${dataset}/items/${itemId}`), label: "Reload the item" };
  return saveItem(form, (c) => api.editItem(dataset, itemId, rev, c), (e) => (e.status === 409 ? reload : undefined), dataset);
}

export async function deleteItemAction(dataset: string, itemId: string, rev: number): Promise<FormState> {
  const at = await projectLinks();
  const r = await api.deleteItem(dataset, itemId, rev);
  if (!r.ok) {
    return { error: r.error.error, link: { href: at(`/datasets/${dataset}/items/${itemId}`), label: "Reload the item" } };
  }
  redirect(at(`/datasets/${dataset}`));
}

export async function publishAction(dataset: string): Promise<FormState> {
  const r = await api.publish(dataset);
  if (!r.ok) return { error: r.error.error };
  refresh();
  return { message: `Published v${r.data.version}` };
}

/** Add to dataset: the chosen dataset, or, when none is chosen, a new one named in the form. */
export async function addTraceAction(traceId: string, _: FormState, form: FormData): Promise<FormState> {
  const at = await projectLinks();
  const dataset = text(form, "dataset");
  if (!dataset) {
    // One request: if the trace can't be copied, no empty dataset is left behind.
    const newName = text(form, "new_name").trim();
    if (!newName) return { error: "name the new dataset" };
    const created = await api.createDataset(newName, "", traceId);
    if (!created.ok) return refusal(created.error, { new_name: newName });
    refresh();
    return { message: "Added.", link: { href: at(`/datasets/${created.data.id}`), label: "Open the new dataset" } };
  }
  const r = await api.addFromTrace(dataset, traceId);
  if (!r.ok) {
    const existing = r.error.item_id;
    return {
      error: r.error.error,
      link: existing ? { href: at(`/datasets/${dataset}/items/${existing}`), label: "Open the item" } : undefined,
    };
  }
  refresh();
  return {
    message: "Added.",
    link: { href: at(`/datasets/${dataset}/items/${r.data.item_id}`), label: "Fill in what should have happened" },
  };
}
