import { notFound } from "next/navigation";
import { Suspense } from "react";
import { currentProject } from "@/lib/auth";
import { Nav } from "@/app/account/nav";

/** Every project page: the nav, and a 404 for a slug the user's org doesn't have. */
export default function ProjectLayout({ children }: LayoutProps<"/p/[slug]">) {
  return (
    <>
      <Nav inProject />
      <Suspense>
        <KnownProject />
      </Suspense>
      {children}
    </>
  );
}

/** Renders nothing; a slug that isn't one of the org's projects becomes the not-found page. */
async function KnownProject() {
  if (!(await currentProject())) notFound();
  return null;
}
