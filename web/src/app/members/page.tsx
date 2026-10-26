import { Suspense } from "react";
import { connection } from "next/server";
import { listMembers, me, type User } from "@/lib/auth";
import { formatTime } from "@/lib/format";
import { Nav } from "@/app/account/nav";
import { updateMemberAction } from "@/app/settings-actions";
import { InviteForm, ResetLinkForm } from "@/app/settings-forms";

/** The org's members (ADR-0032). Owners only; the org always keeps an active owner. */
export default function MembersPage() {
  return (
    <>
      <Nav />
      <main className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6">
        <h1 className="text-2xl font-semibold tracking-tight">Members</h1>
        <Suspense fallback={<p className="mt-6 text-sm text-muted">Loading…</p>}>
          <Members />
        </Suspense>
      </main>
    </>
  );
}

async function Members() {
  await connection();
  const user = await me();
  if (user.role !== "owner") return <p className="mt-6 text-sm text-muted">Only an owner can manage members.</p>;
  const members = await listMembers();
  return (
    <>
      <section className="mt-6" aria-label="Invite">
        <h2 className="text-sm font-medium">Invite someone</h2>
        <p className="mt-1 text-xs text-muted">AgentTrace sends no email: copy the link to them.</p>
        <InviteForm />
      </section>
      <div className="mt-8 overflow-x-auto rounded-md border border-line">
        <table className="w-full text-left text-sm" aria-label="Members">
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Email</th>
              <th className="px-3 py-2 font-medium">Role</th>
              <th className="px-3 py-2 font-medium">Joined</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {members.map((m) => (
              <MemberRow key={m.id} member={m} me={user} lastOwner={isLastOwner(members, m)} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Ingest refuses to leave the org without an active owner, so those buttons aren't offered. */
function isLastOwner(members: User[], m: User): boolean {
  return m.role === "owner" && !m.deactivated && members.filter((o) => o.role === "owner" && !o.deactivated).length === 1;
}

function MemberRow({ member, me, lastOwner }: { member: User; me: User; lastOwner: boolean }) {
  const other = member.role === "owner" ? "member" : "owner";
  return (
    <tr data-member={member.email} className={member.deactivated ? "text-muted" : ""}>
      <td className="px-3 py-2">
        {member.email}
        {member.id === me.id && <span className="ml-2 text-xs text-muted">(you)</span>}
      </td>
      <td className="px-3 py-2 font-mono text-xs">{member.deactivated ? "deactivated" : member.role}</td>
      <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">{formatTime(new Date(member.created_at))}</td>
      <td className="space-x-3 px-3 py-2 text-right text-xs">
        {member.deactivated ? (
          <form action={updateMemberAction.bind(null, member.id, { deactivated: false })} className="inline">
            <button className="underline">Reactivate</button>
          </form>
        ) : (
          <>
            {!lastOwner && (
              <form action={updateMemberAction.bind(null, member.id, { role: other })} className="inline">
                <button className="underline">Make {other}</button>
              </form>
            )}
            <ResetLinkForm userId={member.id} />
            {!lastOwner && (
              <form action={updateMemberAction.bind(null, member.id, { deactivated: true })} className="inline">
                <button className="underline text-bad">Deactivate</button>
              </form>
            )}
          </>
        )}
      </td>
    </tr>
  );
}
