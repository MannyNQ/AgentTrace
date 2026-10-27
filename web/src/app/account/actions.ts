"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import * as api from "@/lib/auth";
import { PUBLIC_URL } from "@/lib/ingest";
import { DEFAULT_PROJECT, PROJECT_COOKIE, SESSION_COOKIE, safeNext } from "@/lib/session";

// Signing in, out, and up. Ingest decides; these set or clear the session cookie (ADR-0032).

export type AuthState = { error?: string; message?: string; email?: string };

function text(form: FormData, key: string): string {
  const v = form.get(key);
  return typeof v === "string" ? v : "";
}

async function keepSession(session: api.NewSession): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, session.token, {
    httpOnly: true,
    sameSite: "lax",
    secure: PUBLIC_URL.startsWith("https:"),
    path: "/",
    expires: new Date(session.expires_at),
  });
}

/** The last project opened, so the address bar shows where the user landed. */
async function home(): Promise<string> {
  return `/p/${(await cookies()).get(PROJECT_COOKIE)?.value || DEFAULT_PROJECT}`;
}

export async function loginAction(next: string | null, _: AuthState, form: FormData): Promise<AuthState> {
  const email = text(form, "email");
  const r = await api.login(email, text(form, "password"));
  if (!r.ok) return { error: r.error.error, email };
  await keepSession(r.data);
  redirect(safeNext(next) ?? (await home()));
}

export async function setupAction(_: AuthState, form: FormData): Promise<AuthState> {
  const email = text(form, "email");
  const r = await api.claim(text(form, "token").trim(), email, text(form, "password"));
  if (!r.ok) return { error: r.error.error, email };
  await keepSession(r.data);
  redirect(await home());
}

export async function acceptInviteAction(token: string, _: AuthState, form: FormData): Promise<AuthState> {
  const email = text(form, "email");
  const r = await api.acceptInvite(token, email, text(form, "password"));
  if (!r.ok) return { error: r.error.error, email };
  await keepSession(r.data);
  redirect(await home());
}

export async function resetPasswordAction(token: string, _: AuthState, form: FormData): Promise<AuthState> {
  const r = await api.resetPassword(token, text(form, "password"));
  if (!r.ok) return { error: r.error.error };
  redirect("/login?reset=1");
}

export async function changePasswordAction(_: AuthState, form: FormData): Promise<AuthState> {
  const r = await api.changePassword(text(form, "current"), text(form, "new"));
  if (!r.ok) return { error: r.error.error };
  return { message: "Password changed. Your other sessions are signed out." };
}

export async function logoutAction(): Promise<void> {
  await api.logout(); // an already-expired session redirects to /login on its own
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}
