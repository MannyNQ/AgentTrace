import "server-only";
import { ingest } from "@/lib/ingest";

// Accounts, members, API keys, and projects (ADR-0032, ADR-0033, ADR-0034). The rules live in
// ingest; these only shape the calls.

export type Role = "owner" | "member";
export type Scope = "ingest" | "full";

export type User = {
  id: string;
  org_id: string;
  email: string;
  role: Role;
  created_at: string;
  deactivated: boolean;
};

export type NewSession = { token: string; expires_at: string; user: User };
export type NewLink = { token: string; kind: "invite" | "reset"; expires_at: string };
export type Link = { kind: "invite" | "reset"; role: Role | null; expires_at: string };

export type KeyInfo = {
  id: string;
  name: string;
  scope: Scope;
  hint: string | null;
  from_env: boolean;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type Project = {
  id: string;
  slug: string;
  name: string;
  retention_days: number;
  created_at: string;
};

const anon = ingest("/v1/auth", { anonymous: true });
const signedIn = ingest("/v1/auth");
const members = ingest("/v1/members");
const keys = ingest("/v1/keys");
const projects = ingest("/v1/projects");
const project = ingest("/v1/project");

export const setupState = () => anon.call<{ claimed: boolean }>("GET", "/setup");
export const claim = (token: string, email: string, password: string) =>
  anon.call<NewSession>("POST", "/setup", { token, email, password });
export const login = (email: string, password: string) =>
  anon.call<NewSession>("POST", "/login", { email, password });
export const link = (token: string) => anon.call<Link>("GET", `/links/${encodeURIComponent(token)}`);
export const acceptInvite = (token: string, email: string, password: string) =>
  anon.call<NewSession>("POST", `/links/${encodeURIComponent(token)}/accept`, { email, password });
export const resetPassword = (token: string, password: string) =>
  anon.call<void>("POST", `/links/${encodeURIComponent(token)}/reset`, { password });

export const logout = () => signedIn.call<void>("POST", "/logout");
export async function me(): Promise<User> {
  const r = await signedIn.call<{ user: User }>("GET", "/me");
  if (!r.ok) throw new Error(r.error.error);
  return r.data.user;
}
export const changePassword = (current: string, next: string) =>
  signedIn.call<void>("POST", "/password", { current, new: next });

export const listMembers = async () => (await members.read<{ members: User[] }>(""))?.members ?? [];
export const updateMember = (id: string, patch: { role?: Role; deactivated?: boolean }) =>
  members.call<User>("PATCH", `/${encodeURIComponent(id)}`, patch);
export const invite = (role: Role) => members.call<NewLink>("POST", "/invites", { role });
export const resetLink = (id: string) => members.call<NewLink>("POST", `/${encodeURIComponent(id)}/reset`);

export const listKeys = async () => (await keys.read<{ keys: KeyInfo[] }>(""))?.keys ?? [];
export const createKey = (name: string, scope: Scope) =>
  keys.call<{ key: string; info: KeyInfo }>("POST", "", { name, scope });
export const revokeKey = (id: string) => keys.call<KeyInfo>("POST", `/${encodeURIComponent(id)}/revoke`);

export const listProjects = async () => (await projects.read<{ projects: Project[] }>(""))?.projects ?? [];
export const createProject = (name: string) => projects.call<Project>("POST", "", { name });
/** The project in the URL, or null when the user's org has no project with that slug. */
export const currentProject = () => project.read<Project>("");
export const updateProject = (patch: { name?: string; retention_days?: number }) =>
  project.call<Project>("PATCH", "", patch);
