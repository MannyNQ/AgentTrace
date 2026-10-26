"use client";

import { useActionState } from "react";
import {
  acceptInviteAction,
  changePasswordAction,
  loginAction,
  resetPasswordAction,
  setupAction,
  type AuthState,
} from "./actions";

type Action = (state: AuthState, form: FormData) => Promise<AuthState>;

const field = "mt-1 block w-full rounded border border-line bg-background px-2 py-1 text-sm";
const button =
  "rounded border border-line px-3 py-1.5 text-sm font-medium hover:bg-line/40 disabled:opacity-50";

function Form({
  action,
  label,
  submit,
  children,
}: {
  action: Action;
  label: string;
  submit: string;
  children: (state: AuthState) => React.ReactNode;
}) {
  const [state, run, pending] = useActionState(action, {});
  return (
    <form action={run} aria-label={label} className="mt-6 space-y-4">
      {children(state)}
      <p aria-live="polite" className="text-sm" data-form-notice>
        {state.error && <span className="text-bad">{state.error}</span>}
        {state.message && <span className="text-good">{state.message}</span>}
      </p>
      <button disabled={pending} className={button}>
        {pending ? "…" : submit}
      </button>
    </form>
  );
}

function Email({ state }: { state: AuthState }) {
  return (
    <label className="block text-xs text-muted">
      Email
      <input name="email" type="email" required autoComplete="email" defaultValue={state.email} className={field} />
    </label>
  );
}

function Password({ name = "password", label = "Password", fresh = false }) {
  return (
    <label className="block text-xs text-muted">
      {label}
      <input
        name={name}
        type="password"
        required
        minLength={fresh ? 12 : undefined}
        autoComplete={fresh ? "new-password" : "current-password"}
        className={field}
      />
      {fresh && <span className="mt-1 block">At least 12 characters.</span>}
    </label>
  );
}

export function LoginForm({ next }: { next: string | null }) {
  return (
    <Form action={loginAction.bind(null, next)} label="Sign in" submit="Sign in">
      {(state) => (
        <>
          <Email state={state} />
          <Password />
        </>
      )}
    </Form>
  );
}

export function SetupForm() {
  return (
    <Form action={setupAction} label="Create the owner account" submit="Create owner account">
      {(state) => (
        <>
          <label className="block text-xs text-muted">
            Setup token
            <input name="token" required autoComplete="off" className={`${field} font-mono`} />
            <span className="mt-1 block">
              From <code className="font-mono">docker compose logs ingest</code>, or the{" "}
              <code className="font-mono">AGENTTRACE_SETUP_TOKEN</code> you set.
            </span>
          </label>
          <Email state={state} />
          <Password fresh />
        </>
      )}
    </Form>
  );
}

export function InviteForm({ token }: { token: string }) {
  return (
    <Form action={acceptInviteAction.bind(null, token)} label="Accept invite" submit="Create account">
      {(state) => (
        <>
          <Email state={state} />
          <Password fresh />
        </>
      )}
    </Form>
  );
}

export function ResetForm({ token }: { token: string }) {
  return (
    <Form action={resetPasswordAction.bind(null, token)} label="Set a new password" submit="Set password">
      {() => <Password label="New password" fresh />}
    </Form>
  );
}

export function ChangePasswordForm() {
  return (
    <Form action={changePasswordAction} label="Change password" submit="Change password">
      {() => (
        <>
          <Password name="current" label="Current password" />
          <Password name="new" label="New password" fresh />
        </>
      )}
    </Form>
  );
}
