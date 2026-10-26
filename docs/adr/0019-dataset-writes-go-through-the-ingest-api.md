# 19. Dataset writes go through the ingest API

Status: accepted. Amends ADR-0018 for writes; amended by ADR-0031 (the web app no longer holds an API key).

## Context

Phase 2 adds the first writes that don't come from the SDK: creating datasets, editing draft items, adding traces, and publishing. The web app is one client. The Phase 3 CLI is another: it reads published versions, and teams will want to manage datasets from code. ADR-0018 has the web app read the stores directly, which suits reads with one UI and no auth, but two writers each enforcing the dataset rules would drift.

## Decision

All dataset reads and writes go through `/v1/datasets` in the ingest service, authenticated with Bearer API keys like `/v1/spans` (ADR-0017). The key resolves the project; every query is scoped to it. The rules live in `agenttrace_ingest.datasets`, behind a store interface.

The web app calls the API from server actions and server components, with `INGEST_URL` and `AGENTTRACE_API_KEY` read from server-only environment variables. Trace reads stay direct (ADR-0018).

## Consequences

- One place enforces the dataset rules, for the UI and the CLI alike.
- The web server holds an API key. It never reaches the browser, but anyone who can reach the web app can use it through the UI: no worse than today before auth (ADR-0002), and Phase 6 replaces it with user sessions.
- The ingest service is now the project's API, not only its span intake.
- Postgres schema changes ship as ingest migrations, applied on startup.
