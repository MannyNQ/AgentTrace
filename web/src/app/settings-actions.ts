"use server";

import { redirect } from "next/navigation";
import { refresh } from "next/cache";
import * as api from "@/lib/auth";
import { PUBLIC_ENDPOINT, PUBLIC_URL } from "@/lib/ingest";
import { keyEnvLines } from "@/lib/key-env";

// Owner-only settings: projects, API keys, and members (ADR-0032, ADR-0033). Ingest refuses a
// member, so these don't check the role themselves.

export type SettingsState = {
  error?: string;
  message?: string;
  /** Shown once: a new API key, or an invite or reset link. */
  secret?: string;
  /** Shown once with a new key: the environment to put it in. */
  env?: string;
};

function text(form: FormData, key: string): string {
  const v = form.get(key);
  return typeof v === "string" ? v : "";
}

export async function updateProjectAction(_: SettingsState, form: FormData): Promise<SettingsState> {
  const days = Number(text(form, "retention_days"));
  const r = await api.updateProject({ name: text(form, "name"), retention_days: days });
  if (!r.ok) return { error: r.error.fields ? Object.values(r.error.fields).join("; ") : r.error.error };
  refresh();
  return { message: "Saved. New retention applies to spans ingested from now on." };
}

export async function createKeyAction(_: SettingsState, form: FormData): Promise<SettingsState> {
  const scope = text(form, "scope") === "ingest" ? "ingest" : "full";
  const r = await api.createKey(text(form, "name"), scope);
  if (!r.ok) return { error: r.error.error };
  refresh();
  return {
    message: `Key "${r.data.info.name}" created. Copy it now; it won't be shown again.`,
    secret: r.data.key,
    env: keyEnvLines(r.data.key, scope, PUBLIC_ENDPOINT),
  };
}

export async function revokeKeyAction(id: string): Promise<void> {
  const r = await api.revokeKey(id);
  if (!r.ok) throw new Error(r.error.error);
  refresh();
}

export async function createProjectAction(_: SettingsState, form: FormData): Promise<SettingsState> {
  const r = await api.createProject(text(form, "name"));
  if (!r.ok) return { error: r.error.fields ? Object.values(r.error.fields).join("; ") : r.error.error };
  redirect(`/p/${r.data.slug}`);
}

export async function inviteAction(_: SettingsState, form: FormData): Promise<SettingsState> {
  const role = text(form, "role") === "owner" ? "owner" : "member";
  const r = await api.invite(role);
  if (!r.ok) return { error: r.error.error };
  return {
    message: `Invite for a new ${role}, valid for 7 days and usable once. Send it to them yourself.`,
    secret: `${PUBLIC_URL}/invite/${r.data.token}`,
  };
}

export async function resetLinkAction(id: string): Promise<SettingsState> {
  const r = await api.resetLink(id);
  if (!r.ok) return { error: r.error.error };
  return { message: "Reset link, valid for 7 days and usable once:", secret: `${PUBLIC_URL}/reset/${r.data.token}` };
}

export async function updateMemberAction(id: string, patch: { role?: api.Role; deactivated?: boolean }): Promise<void> {
  const r = await api.updateMember(id, patch);
  if (!r.ok) throw new Error(r.error.error);
  refresh();
}
