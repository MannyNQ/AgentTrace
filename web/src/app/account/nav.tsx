import Link from "next/link";
import { Suspense } from "react";
import { currentProject, me, type Project, type User } from "@/lib/auth";
import { logoutAction } from "./actions";

/** The bar above every signed-in page; `inProject` adds the project's own links. It reads the
 * session, so it streams in behind a boundary. */
export function Nav({ inProject = false }: { inProject?: boolean }) {
  return (
    <Suspense fallback={<nav className="h-10 border-b border-line" />}>
      <NavBar inProject={inProject} />
    </Suspense>
  );
}

async function NavBar({ inProject }: { inProject: boolean }) {
  const [user, project] = await Promise.all([me(), inProject ? currentProject() : null]);
  return <Bar user={user} project={project} />;
}

function Bar({ user, project }: { user: User; project: Project | null }) {
  const link = "hover:underline";
  return (
    <nav className="border-b border-line text-sm" aria-label="Main">
      <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2 sm:px-6">
        <Link href="/" className="font-semibold">
          AgentTrace
        </Link>
        {project && (
          <>
            <Link href={`/p/${project.slug}`} className="font-mono text-xs" data-project={project.slug}>
              {project.name}
            </Link>
            <Link href={`/p/${project.slug}/overview`} className={link}>
              Overview
            </Link>
            <Link href={`/p/${project.slug}/traces`} className={link}>
              Traces
            </Link>
            <Link href={`/p/${project.slug}/datasets`} className={link}>
              Datasets
            </Link>
            <Link href={`/p/${project.slug}/experiments`} className={link}>
              Experiments
            </Link>
            <Link href={`/p/${project.slug}/settings`} className={link}>
              Settings
            </Link>
          </>
        )}
        <span className="ml-auto flex items-center gap-4 text-xs text-muted">
          <Link href="/projects" className={link}>
            Projects
          </Link>
          {user.role === "owner" && (
            <Link href="/members" className={link}>
              Members
            </Link>
          )}
          <Link href="/account/password" className={link} data-user={user.email}>
            {user.email}
          </Link>
          <form action={logoutAction}>
            <button className="hover:underline">Sign out</button>
          </form>
        </span>
      </div>
    </nav>
  );
}
