import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { currentProject } from "@/lib/auth";
import { projectLinks } from "@/lib/links";

export default function ProjectHome() {
  return (
    <main className="mx-auto w-full max-w-2xl px-4 py-12 sm:px-6">
      <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
        <Home />
      </Suspense>
    </main>
  );
}

async function Home() {
  await connection();
  const [project, at] = await Promise.all([currentProject(), projectLinks()]);
  if (!project) return null; // the layout shows not-found
  return (
    <>
      <p className="font-mono text-xs uppercase tracking-widest text-muted">Project</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">{project.name}</h1>
      <p className="mt-3 text-muted">
        Observability and evals for LLM agents. Send traces with an API key from{" "}
        <Link href={at("/settings")} className="underline">
          Settings
        </Link>
        , and run <code className="font-mono">agenttrace eval</code> to grade your agent on a dataset.
      </p>
      <p className="mt-6">
        <Link href={at("/traces")} className="font-medium underline">
          Browse traces →
        </Link>
        <Link href={at("/datasets")} className="ml-6 font-medium underline">
          Datasets →
        </Link>
        <Link href={at("/experiments")} className="ml-6 font-medium underline">
          Experiments →
        </Link>
      </p>
      <p className="mt-10 text-sm text-muted">
        <Link href="/status" className="hover:underline">
          Service status
        </Link>
      </p>
    </>
  );
}
