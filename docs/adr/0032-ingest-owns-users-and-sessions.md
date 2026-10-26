# 32. Ingest owns users and sessions; session requests name the project in a header

Status: accepted. Builds on ADR-0031.

## Context

Phase 6 adds user accounts for the web app. Two shapes were possible: Next.js owns sessions (Auth.js or similar) and calls ingest with a service key and a trusted "acting user" header, or ingest owns users and sessions and the web app forwards a session token. With the first, anyone holding the service key can act as any user, and ingest can't tell a user from the web server.

API keys resolve a project (ADR-0017). A user can belong to many projects, so a session request has to say which one it means.

## Decision

- Ingest holds users, argon2id password hashes, org memberships, and sessions in Postgres. A session is an opaque random token stored hashed.
- The web app's login form calls `/v1/auth/login`, keeps the token in an httpOnly cookie, and forwards it to ingest on every call. The browser never talks to ingest.
- Ingest's auth dependency accepts either an API key or a session and hands every handler a resolved `project_id`, as handlers get today.
- Session requests keep the same `/v1/...` paths and name the project in `AgentTrace-Project: <slug>`. Ingest checks membership. A key request that sends a header naming a different project than its key gets 403.
- Users belong to one org with a role, `owner` or `member`. Members see every project in the org. There are no per-project access lists, and the UI assumes one org per install.

## Consequences

- One route table serves the SDK, the CLI, and the web app.
- Identity has one home, so OIDC later is a new way to create a session, not a second user store.
- The web app can't act as a user without that user's session.
- Ingest grows login, setup, invite, and member endpoints alongside the data API.
