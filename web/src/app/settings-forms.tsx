"use client";

import { useActionState } from "react";
import {
  createKeyAction,
  createProjectAction,
  inviteAction,
  resetLinkAction,
  updateProjectAction,
  type SettingsState,
} from "./settings-actions";

const field = "mt-1 block rounded border border-line bg-background px-2 py-1 text-sm";
const button = "rounded border border-line px-3 py-1 text-sm font-medium hover:bg-line/40 disabled:opacity-50";

function Notice({ state }: { state: SettingsState }) {
  return (
    <div aria-live="polite" className="text-sm" data-form-notice>
      {state.error && <p className="text-bad">{state.error}</p>}
      {state.message && <p className="text-good">{state.message}</p>}
      {state.secret && (
        <input
          readOnly
          value={state.secret}
          onFocus={(e) => e.currentTarget.select()}
          className={`${field} mt-2 w-full font-mono`}
          data-secret
        />
      )}
      {state.env && (
        <label className="mt-3 block text-xs text-muted">
          Environment
          <textarea
            readOnly
            value={state.env}
            rows={state.env.split("\n").length}
            onFocus={(e) => e.currentTarget.select()}
            className={`${field} w-full resize-none font-mono text-xs`}
            data-key-env
          />
        </label>
      )}
    </div>
  );
}

export function ProjectForm({ name, retentionDays }: { name: string; retentionDays: number }) {
  const [state, action, pending] = useActionState(updateProjectAction, {});
  return (
    <form action={action} aria-label="Project" className="mt-3 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted">
          Name
          <input name="name" required defaultValue={name} className={`${field} w-56`} />
        </label>
        <label className="text-xs text-muted">
          Retention (days)
          <input
            name="retention_days"
            type="number"
            min={1}
            max={3650}
            required
            defaultValue={retentionDays}
            className={`${field} w-28`}
          />
        </label>
        <button disabled={pending} className={button}>
          Save
        </button>
      </div>
      <Notice state={state} />
    </form>
  );
}

export function CreateKeyForm() {
  const [state, action, pending] = useActionState(createKeyAction, {});
  return (
    <form action={action} aria-label="New API key" className="mt-3 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted">
          Name
          <input name="name" required placeholder="prod agent" className={`${field} w-56`} />
        </label>
        <label className="text-xs text-muted">
          Scope
          <select name="scope" defaultValue="ingest" className={field}>
            <option value="ingest">ingest: send spans only (agents in production)</option>
            <option value="full">full: the whole project API (CLI and CI)</option>
          </select>
        </label>
        <button disabled={pending} className={button}>
          Create key
        </button>
      </div>
      <Notice state={state} />
    </form>
  );
}

export function CreateProjectForm() {
  const [state, action, pending] = useActionState(createProjectAction, {});
  return (
    <form action={action} aria-label="New project" className="mt-3 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted">
          Name
          <input name="name" required placeholder="Support bot" className={`${field} w-64`} />
          <span className="mt-1 block">The URL slug comes from the name and never changes.</span>
        </label>
        <button disabled={pending} className={button}>
          Create project
        </button>
      </div>
      <Notice state={state} />
    </form>
  );
}

export function InviteForm() {
  const [state, action, pending] = useActionState(inviteAction, {});
  return (
    <form action={action} aria-label="Invite" className="mt-3 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-muted">
          Role
          <select name="role" defaultValue="member" className={field}>
            <option value="member">member</option>
            <option value="owner">owner</option>
          </select>
        </label>
        <button disabled={pending} className={button}>
          Create invite link
        </button>
      </div>
      <Notice state={state} />
    </form>
  );
}

export function ResetLinkForm({ userId }: { userId: string }) {
  const [state, action, pending] = useActionState(() => resetLinkAction(userId), {});
  return (
    <form action={action} className="inline">
      <button disabled={pending} className="text-xs underline">
        Reset link
      </button>
      <Notice state={state} />
    </form>
  );
}
