import type { ReactNode } from "react";

/** The frame for the pages before sign-in. */
export function AuthPage({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto w-full max-w-sm px-4 py-16 sm:px-6">
      <p className="font-mono text-xs uppercase tracking-widest text-muted">AgentTrace</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{title}</h1>
      {children}
    </main>
  );
}
