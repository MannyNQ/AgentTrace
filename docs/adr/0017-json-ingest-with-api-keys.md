# 17. Phase 1 ingest: JSON spans, whole-batch validation, API keys resolve the project

Status: accepted

## Context

Phase 1 needs one way for the SDK to send spans. OTLP is the standard, but its mapping can wait. Every span needs a `project_id` (ADR-0002) before auth exists (Phase 6).

## Decision

`POST /v1/spans` takes `{"spans": [...]}`, each matching `agenttrace.Span`, up to 1000 spans and 5 MB. If any span is invalid, the whole batch is rejected with 422; a valid batch is queued and answered with `202 {"accepted": n}`.

Requests carry `Authorization: Bearer <key>`. Keys live in a Postgres `api_keys` table as SHA-256 hashes, each mapping to a project. On startup, ingest upserts the key from `AGENTTRACE_API_KEY` for the default project, so a fresh install works with the key in `.env`.

## Consequences

- An invalid span is an SDK bug, and it shows up loudly instead of being dropped silently.
- OTLP/HTTP is a separate endpoint in a later phase.
- Phase 6 adds key management on top of the same table.
