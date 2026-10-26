# 33. API keys have a scope: ingest or full

Status: accepted. Builds on ADR-0017.

## Context

Every key can currently do everything in its project. SDK keys sit inside production agents, often in many places, while CLI and CI keys create experiments and replays and edit datasets. A key leaked from a production agent shouldn't be able to read traces or change datasets.

## Decision

- Each key has a scope. `ingest` keys can call `POST /v1/spans` only. `full` keys can call the whole project API.
- Keys have a name, a scope, a visible prefix (`at_…` plus the last 4 characters), `created_by`, `last_used_at`, and `revoked_at`. A key is shown once, at creation, and stored as a SHA-256 hash. Revoking is a soft delete.
- `last_used_at` is written at most once a minute per key, so span ingest doesn't write to Postgres on every batch.
- Only owners create and revoke keys. Keys belong to the project, so they survive the deactivation of the member who created them.
- The key from `AGENTTRACE_API_KEY` is upserted as a `full` key for the default project on startup, as today. The UI labels it "from .env" and disables Revoke, since a restart would bring it back.
- A request outside the key's scope gets 403 with an error naming the scope it needs, and the CLI prints it.

## Consequences

- Upgraded installs keep working: the `.env` key is `full`.
- The SDK should be given an `ingest` key, and the README and examples say so.
- Rotating the `.env` key means changing `.env`, not clicking in the UI.
