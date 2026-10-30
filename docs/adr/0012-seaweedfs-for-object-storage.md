# 12. SeaweedFS for local object storage

Status: accepted

## Context

The overview planned MinIO for S3-compatible payload storage (ADR-0009) in the self-hosted stack. MinIO no longer publishes community images to Docker Hub or Quay, so `docker compose up` can't pull it.

## Decision

The compose stack uses SeaweedFS (`chrislusf/seaweedfs`, Apache 2.0) with its S3 gateway on port 8333. Services talk to it only through the S3 API (boto3), never through SeaweedFS-specific APIs.

## Consequences

- Swapping in real S3, R2, or any S3-compatible store is a change of endpoint and credentials, nothing else.
- The S3 credentials are `S3_ACCESS_KEY` / `S3_SECRET_KEY` in `.env`; `sdk/python/src/trayce/stack/seaweedfs-start.sh` writes SeaweedFS's identity from them on every start, so changing them is one edit and no tracked file holds the secret.
- The ingest service creates the payload bucket on startup if it's missing, so there's no separate init container.
