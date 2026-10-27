# Auth

Every `/v1` request names who is calling: `Authorization: Bearer <key>` for an API key, or `Authorization: Session <token>` with `AgentTrace-Project: <slug>` for a signed-in user (ADR-0032). A fresh install is claimed in the web app with a setup token from ingest's logs (ADR-0034). Keys belong to one project and have a scope, `ingest` (spans only) or `full` (ADR-0033). Users belong to the org as `owner` or `member`. Members use every project; only owners manage members, keys, and project settings. The web app signs users in, keeps the session in an httpOnly cookie, puts each project's pages under `/p/<slug>/`, and reads and writes only through ingest, holding no store credentials (ADR-0031).

## Sub-features

- `au-claim` an unclaimed install sends every page to `/login`, and `/setup` creates the owner with the setup token that ingest logs (`no owner yet; claim this install in the web app with setup token …`). A wrong token is refused; once claimed, `/setup` refuses again and `GET /v1/auth/setup` says `claimed: true`.
- `au-session` `/login` signs in (wrong password and unknown email read the same), "Sign out" ends the session, and an expired or signed-out session lands back on `/login`.
- `au-members` an owner makes an invite link on `/members`; `/invite/<token>` creates the account once with the role chosen. A member sees every project but gets "Only an owner can …" on `/members` and `/p/<slug>/settings`, and 403 from `/v1/keys`.
- `au-projects` an owner creates a project on `/projects` and gets a slug from its name; `/p/<slug>/settings` creates a key, shown once, with scope `ingest` or `full`.
- `au-isolation` spans sent with project 2's key are stored in project 2 only: project 1's trace list doesn't show them, and its key and its session get 404 for the trace.
- `au-scope` an `ingest` key sends spans (202) and gets 403 `this needs a full API key; this one's scope is ingest` elsewhere, such as `/v1/datasets`.
- `au-redirect` a path from before projects were in URLs (`/replays/<id>`, `/traces/<id>`, `/experiments/<id>`, `/datasets/<id>`) redirects to `/p/<slug>/…` of the entity's project after sign-in; `/` goes to the last project opened.
- `au-web-env` the web app reads only `INGEST_URL` and `AGENTTRACE_PUBLIC_URL`, has no store client among its dependencies, and compose has no web service holding store credentials; the browser's requests all go to the web app, never to ingest.
- `au-tests` `services/ingest/tests/test_auth_matrix.py` pins every credential against every route kind; `test_isolation_integration.py` calls every project route at another project's ids with that project's key and session.

## How to get to it (user POV)

- The web app: `/setup` on a new install, `/login`, `/members` (invite), `/invite/<token>`, `/projects` (new project), `/p/<slug>/settings` (API keys), the project picker and "Sign out" in the top bar.
- The SDK and CLI: `AGENTTRACE_API_KEY` set to a project's key.
- The API: `curl -H "Authorization: Bearer <key>" …`, or `-H "Authorization: Session <token>" -H "AgentTrace-Project: <slug>"` with a token from `POST /v1/auth/login`.

## Driving it with agenttrace-verify

Preconditions:

- Start with `CLAIM=0 H up`, so the install is unclaimed. `H traces` refuses on such a run; use the browser pane and `curl`.
- The owner is `verify-owner@example.com` / `verify-owner-password` and the member `verify-member@example.com` / `verify-member-password`, both test credentials for this local instance.
- The demo agent step needs `ANTHROPIC_API_KEY` in `.env`. Without it, send project 2's spans with `H emit` and `AGENTTRACE_API_KEY` set to project 2's key, and report the demo-agent journey as not verified.

- **Unclaimed.** `curl -s :18000/v1/auth/setup` is `{"claimed": false}`. The browser pane on `http://127.0.0.1:13000/p/default/traces` lands on `/login?next=…`.
- **Claim.** `docker logs agenttrace-verify-ingest-1 2>&1 | grep 'setup token'` gives the token. On `/setup`, a wrong token shows `wrong setup token; it's in ingest's logs or AGENTTRACE_SETUP_TOKEN`; the logged one with the owner's email and password lands on `/p/default`. `GET /v1/auth/setup` is now `{"claimed": true}`, and claiming again is 409.
- **Invite.** `/members` → "Invite someone", role `member` → "Create invite link"; the link is in the form's read-only field. Sign out, open the link, create the member account: it lands on `/p/default`. `/members` says "Only an owner can manage members." and `/p/default/settings` says "Only an owner can change a project's settings or its API keys." Sign out.
- **Project 2.** As the owner, `/projects` → "New project" named `Project Two` → "Create project": it opens `/p/project-two`. On `/p/project-two/settings`, create a key with scope `ingest` (the default); copy it as `$K2` from the read-only field (it's shown once; the keys table then shows only its last four characters).
- **Spans into project 2.** `AGENTTRACE_API_KEY=$K2 AGENTTRACE_ENDPOINT=http://127.0.0.1:18000 uv run demo-agent "Where is order 1042?"` prints the answer. `H ch au "SELECT DISTINCT toString(trace_id), toString(project_id) FROM spans"` shows its trace `$T2` in project 2's id only.
- **Project 1 can't see it.** `/p/project-two/traces` lists `$T2`; `/p/default/traces` doesn't, and `/p/default/traces/$T2` renders the not-found page. `curl :18000/v1/traces/$T2 -H "Authorization: Bearer $KEY"` (the `.env` key, project 1) is 404, as is the owner's session with `AgentTrace-Project: default`; with `AgentTrace-Project: project-two` it's 200, and `$K2` gets 403 (reads need a `full` key).
- **Scope.** `curl :18000/v1/datasets -H "Authorization: Bearer $K2"` is 403 `this needs a full API key; this one's scope is ingest`; `POST /v1/spans` with `$K2` and `{"spans": []}` is 202.
- **Old links.** Run `agenttrace replay` (see [Replay](./replay.md)) or `POST /v1/replays` with the `.env` key to get a replay `$R` in the default project; in the browser pane `http://127.0.0.1:13000/replays/$R` lands on `/p/default/replays/$R`. With a cookie jar holding `agenttrace_session` (a token from `POST /v1/auth/login`), `curl -b <jar> :13000/traces/$T2` answers 307 to `/p/project-two/traces/$T2`. A replay in `project-two` of a default-project trace is 404 `trace not found`.
- **Web environment.** `grep -rhoE "process\.env\.[A-Z_]+" web/src | sort -u` prints only `INGEST_URL` and `AGENTTRACE_PUBLIC_URL`; `web/package.json` names no ClickHouse, Postgres, S3, or Redis client; `docker-compose.yml` has no `web` service. In the browser pane, `read_network_requests` with `urlPattern: "18000"` finds nothing after the journey.
- **Tests.** `uv run pytest services/ingest/tests/test_auth_matrix.py`, then the datasets entry's integration env with `uv run pytest -m integration services/ingest/tests/test_isolation_integration.py`.

## Gotchas

- `up` claims the install unless `CLAIM=0`, and the claim can't be undone without `H down`: a claimed run can't show `/setup`.
- The browser pane keeps cookies across runs of the same profile. Sign out first, or a stale `agenttrace_session` cookie sends you to `/login` with nothing explained.
- The setup token changes on every ingest start while the install is unclaimed. After `store-start ingest`, read the logs again.
- Ten failed sign-ins for one email lock it for 15 minutes (429), counted in Redis. Use a fresh email when testing the lockout, or the owner can't sign in for the rest of the run.
- `ps eww` can't show the web server's environment: `next-server` rewrites its process title. `up` also passes the shell's environment through, so read what the web code uses instead.
- A page for an entity in another project answers HTTP 200 with Next's not-found page (`NEXT_HTTP_ERROR_FALLBACK;404` in the HTML), because the status is sent before the streamed lookup finishes. Check the content, not the status.
- In the browser pane, `form_input` doesn't focus a field, so pressing Return doesn't submit; click the form's button.
