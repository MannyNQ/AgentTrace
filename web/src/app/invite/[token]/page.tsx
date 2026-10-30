import { Suspense } from "react";
import { connection } from "next/server";
import { link } from "@/lib/auth";
import { InviteForm } from "../../account/forms";
import { AuthPage } from "../../account/ui";

type Props = PageProps<"/invite/[token]">;

export default function InvitePage(props: Props) {
  return (
    <AuthPage title="Join Trayce">
      <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
        <Invite params={props.params} />
      </Suspense>
    </AuthPage>
  );
}

async function Invite({ params }: Pick<Props, "params">) {
  await connection();
  const { token } = await params;
  const r = await link(token);
  if (!r.ok || r.data.kind !== "invite") {
    return (
      <p className="mt-6 text-sm text-bad" data-link-gone>
        {r.ok ? "This isn't an invite link." : r.error.error}
      </p>
    );
  }
  return (
    <>
      <p className="mt-3 text-sm text-muted">
        You&apos;re invited as {r.data.role === "owner" ? "an owner" : "a member"}. Pick the email and password
        you&apos;ll sign in with.
      </p>
      <InviteForm token={token} />
    </>
  );
}
