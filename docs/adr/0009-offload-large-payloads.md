# 9. Offload payloads over 8 KB to object storage

Status: accepted

## Context

Prompts and completions can be 100 KB or more. Storing them inline in ClickHouse slows analytics queries that never read them.

## Decision

Span input and output values over 8 KB go to S3/MinIO. The span keeps a 1 KB preview and a `payload_uri`. Smaller values stay inline. Search runs over previews only. Both limits are settings.

## Consequences

- Full-text search can miss text beyond the first 1 KB of a large payload.
- The trace detail view fetches large payloads lazily from object storage.
