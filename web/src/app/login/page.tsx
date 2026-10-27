import { redirect } from "next/navigation";
import { Suspense } from "react";
import { connection } from "next/server";
import { setupState } from "@/lib/auth";
import { safeNext } from "@/lib/session";
import { LoginForm } from "../account/forms";
import { AuthPage } from "../account/ui";

export default function LoginPage(props: PageProps<"/login">) {
  return (
    <AuthPage title="Sign in">
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Login searchParams={props.searchParams} />
      </Suspense>
    </AuthPage>
  );
}

function one(v: string | string[] | undefined): string | undefined {
  return typeof v === "string" ? v : undefined;
}

async function Login({ searchParams }: { searchParams: PageProps<"/login">["searchParams"] }) {
  await connection();
  const params = await searchParams;
  const setup = await setupState();
  if (!setup.ok) {
    return <p className="mt-6 text-sm text-bad">{setup.error.error}</p>;
  }
  if (!setup.data.claimed) redirect("/setup"); // nobody can sign in until the install has an owner
  return (
    <>
      {one(params.expired) && (
        <p className="mt-4 text-sm text-muted" data-session-expired>
          Your session ended. Sign in again.
        </p>
      )}
      {one(params.reset) && <p className="mt-4 text-sm text-good">Password set. Sign in with it.</p>}
      <LoginForm next={safeNext(one(params.next))} />
    </>
  );
}
