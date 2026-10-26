import { Suspense } from "react";
import { connection } from "next/server";
import { link } from "@/lib/auth";
import { ResetForm } from "../../account/forms";
import { AuthPage } from "../../account/ui";

type Props = PageProps<"/reset/[token]">;

export default function ResetPage(props: Props) {
  return (
    <AuthPage title="Set a new password">
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Reset params={props.params} />
      </Suspense>
    </AuthPage>
  );
}

async function Reset({ params }: Pick<Props, "params">) {
  await connection();
  const { token } = await params;
  const r = await link(token);
  if (!r.ok || r.data.kind !== "reset") {
    return (
      <p className="mt-6 text-sm text-bad" data-link-gone>
        {r.ok ? "This isn't a password reset link." : r.error.error}
      </p>
    );
  }
  return (
    <>
      <p className="mt-3 text-sm text-muted">Setting a password signs you out everywhere else.</p>
      <ResetForm token={token} />
    </>
  );
}
