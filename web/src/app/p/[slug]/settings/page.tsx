import { Suspense } from "react";
import { connection } from "next/server";
import { currentProject, listKeys, me, type KeyInfo } from "@/lib/auth";
import { formatTime } from "@/lib/format";
import { revokeKeyAction } from "@/app/settings-actions";
import { CreateKeyForm, ProjectForm } from "@/app/settings-forms";
import { Crumbs } from "../datasets/ui";

/** The project's name and retention, and its API keys (ADR-0033). Owners only. */
export default function SettingsPage() {
  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6">
      <Crumbs trail={[["Settings"]]} />
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Settings</h1>
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Settings />
      </Suspense>
    </main>
  );
}

async function Settings() {
  await connection();
  const [user, project] = await Promise.all([me(), currentProject()]);
  if (!project) return null;
  if (user.role !== "owner") {
    return <p className="mt-6 text-sm text-muted">Only an owner can change a project&apos;s settings or its API keys.</p>;
  }
  const keys = await listKeys();
  return (
    <>
      <section className="mt-6" aria-label="Project">
        <h2 className="text-sm font-medium">Project</h2>
        <p className="mt-1 text-xs text-muted">
          Slug <code className="font-mono">{project.slug}</code>, which stays the same when you rename it.
        </p>
        <ProjectForm name={project.name} retentionDays={project.retention_days} />
      </section>
      <section className="mt-10" aria-label="API keys">
        <h2 className="text-sm font-medium">API keys</h2>
        <p className="mt-1 text-xs text-muted">
          Give agents in production an <code className="font-mono">ingest</code> key, and the CLI and CI a{" "}
          <code className="font-mono">full</code> one, as <code className="font-mono">AGENTTRACE_API_KEY</code>.
        </p>
        <CreateKeyForm />
        <KeyTable keys={keys} />
      </section>
    </>
  );
}

function KeyTable({ keys }: { keys: KeyInfo[] }) {
  if (keys.length === 0) return <p className="mt-4 text-sm text-muted">No keys yet.</p>;
  return (
    <div className="mt-4 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-left text-sm" aria-label="Keys">
        <thead className="border-b border-line text-xs text-muted">
          <tr>
            <th className="px-3 py-2 font-medium">Name</th>
            <th className="px-3 py-2 font-medium">Key</th>
            <th className="px-3 py-2 font-medium">Scope</th>
            <th className="px-3 py-2 font-medium">Created</th>
            <th className="px-3 py-2 font-medium">Last used</th>
            <th className="px-3 py-2" />
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {keys.map((k) => (
            <tr key={k.id} data-key={k.name} className={k.revoked_at ? "text-muted" : ""}>
              <td className="px-3 py-2">{k.name || "(unnamed)"}</td>
              <td className="px-3 py-2 font-mono text-xs">{k.hint ? `…${k.hint}` : "—"}</td>
              <td className="px-3 py-2 font-mono text-xs">{k.scope}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{formatTime(new Date(k.created_at))}</td>
              <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
                {k.last_used_at ? formatTime(new Date(k.last_used_at)) : "never"}
              </td>
              <td className="px-3 py-2 text-right text-xs">
                {k.revoked_at ? (
                  "revoked"
                ) : k.from_env ? (
                  <span title="Re-added from AGENTTRACE_API_KEY on every startup; change it in .env">from .env</span>
                ) : (
                  <form action={revokeKeyAction.bind(null, k.id)}>
                    <button className="underline">Revoke</button>
                  </form>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
