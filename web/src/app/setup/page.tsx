import { redirect } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { setupState } from "@/lib/auth";
import { SetupForm } from "../account/forms";
import { AuthPage } from "../account/ui";

/** Claiming an install: the first account becomes the owner (ADR-0034). */
export default function SetupPage() {
  return (
    <AuthPage title="Create the owner account">
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Setup />
      </Suspense>
    </AuthPage>
  );
}

async function Setup() {
  await connection();
  const setup = await setupState();
  if (!setup.ok) return <p className="mt-6 text-sm text-bad">{setup.error.error}</p>;
  if (setup.data.claimed) redirect("/login");
  return (
    <>
      <p className="mt-3 text-sm text-muted">
        This install has no owner yet. The owner manages members, projects, and API keys. Existing
        traces, datasets, and keys stay as they are, in the default project.
      </p>
      <SetupForm />
    </>
  );
}
