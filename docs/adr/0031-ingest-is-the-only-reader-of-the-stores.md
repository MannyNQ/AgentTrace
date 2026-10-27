# 31. Ingest is the only service that reads the stores

Status: accepted. Supersedes ADR-0018; amends ADR-0019.

## Context

ADR-0018 let the web app query ClickHouse and S3 directly while there was one UI, one project, and no auth, and said Phase 6 would revisit it. Datasets, experiments, and replays already go through the ingest API (ADR-0019, ADR-0023, ADR-0029), so access rules for them live in Python. Adding auth with trace reads still in Next.js would put the same project checks in two languages and two services, the drift ADR-0019 was written to avoid.

## Decision

- Trace, span, and payload reads move behind ingest: `/v1/traces` (list, summary, spans) and the payload fetch that `web/src/app/api/traces/.../route.ts` does today. The queries in `web/src/lib/traces.ts` move with them.
- The web app calls only ingest. Its container gets `INGEST_URL` and nothing else: no ClickHouse, S3, or Postgres credentials and no `AGENTTRACE_API_KEY`.
- The home page's health check reads ingest's `/health`, which stays unauthenticated and reports only up or down per store.

## Consequences

- One service decides who can read what, for the web app, the CLI, and anyone scripting against the API.
- A compromised web container can't read the stores; it can only act as the users whose sessions pass through it.
- Each trace read is one more network hop. Trace pages already wait on ClickHouse, so the hop is small next to the query.
- Trace reads become part of the public API, so their response shapes need the same care as `/v1/datasets`.
