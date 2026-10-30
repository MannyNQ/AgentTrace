"""/v1/analytics: the overview's volume, latency, cost, tools, and models for a range of a
project's traces, filtered as the trace list is (ADR-0037, ADR-0038). Sessions and `full` keys
read it; `ingest` keys can't (ADR-0033)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from trayce_ingest import analytics as an
from trayce_ingest import projects
from trayce_ingest.auth import Auth, ProjectAccess

PREFIX = "/v1/analytics"

Q = Annotated[an.AnalyticsQuery, Query()]


def router(analytics: an.Analytics, a: Auth) -> APIRouter:
    r = APIRouter(prefix=PREFIX)
    auth = Depends(ProjectAccess(a))

    def retention(project: UUID) -> int:
        # Older spans may already have expired, so the window stops at the project's retention.
        return projects.get(a.store, project).retention_days

    @r.get("/volume")
    def volume(project: Annotated[UUID, auth], q: Q) -> an.Volume:
        """Traces per bucket, running ones included; error rates by trace and span kind."""
        return analytics.volume(project, q, retention(project))

    @r.get("/latency")
    def latency(project: Annotated[UUID, auth], q: Q) -> an.Latency:
        """p50, p95, and p99 of completed traces' latency, per bucket and overall."""
        return analytics.latency(project, q, retention(project))

    @r.get("/cost")
    def cost(project: Annotated[UUID, auth], q: Q) -> an.Cost:
        """Spend per bucket by top model, the per-model table, and cost per completed trace."""
        return analytics.cost(project, q, retention(project))

    @r.get("/tools")
    def tools(project: Annotated[UUID, auth], q: Q) -> an.Groups:
        """Each tool's calls, error rate, and latency, and the slowest by p95."""
        return analytics.groups(project, q, retention(project), "tool")

    @r.get("/models")
    def models(project: Annotated[UUID, auth], q: Q) -> an.Groups:
        """Each model's LLM calls, error rate, and latency, and the slowest by p95."""
        return analytics.groups(project, q, retention(project), "llm")

    return r
