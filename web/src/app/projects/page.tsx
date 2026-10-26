import Link from "next/link";
import { Suspense } from "react";
import { connection } from "next/server";
import { listProjects, me } from "@/lib/auth";
import { Nav } from "@/app/account/nav";
import { CreateProjectForm } from "@/app/settings-forms";

/** Every project in the org; every member sees all of them. Owners create projects. */
export default function ProjectsPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
          <Projects />
        </Suspense>
      </main>
    </>
  );
}

async function Projects() {
  await connection();
  const [user, projects] = await Promise.all([me(), listProjects()]);
  return (
    <>
      <ul className="mt-6 divide-y divide-line rounded-md border border-line" aria-label="Projects">
        {projects.map((p) => (
          <li key={p.id} className="flex items-baseline gap-3 p-3 text-sm" data-project={p.slug}>
            <Link href={`/p/${p.slug}`} className="font-medium hover:underline">
              {p.name}
            </Link>
            <span className="font-mono text-xs text-muted">{p.slug}</span>
            <span className="ml-auto text-xs text-muted">{p.retention_days}-day retention</span>
          </li>
        ))}
      </ul>
      {user.role === "owner" && (
        <section className="mt-8">
          <h2 className="text-sm font-medium">New project</h2>
          <CreateProjectForm />
        </section>
      )}
    </>
  );
}
