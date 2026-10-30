"""Pinning (ADR-0021): exempt a trace from retention by rewriting its `retention_days` in place."""

from uuid import UUID

from trayce_ingest.settings import Settings
from trayce_ingest.stores import ProjectReads

# The TTL is `toDateTime(start_time) + toIntervalDay(retention_days)`, and DateTime ends in
# February 2106: past that the sum wraps around to 1970. So a pinned row keeps until 2106-01-01
# rather than for a fixed number of days, which would wrap (65535 days from 2026 lands in 2069).
PINNED_UNTIL = "2106-01-01"
PINNED_RETENTION = f"toUInt16(dateDiff('day', toDate(start_time), toDate('{PINNED_UNTIL}')))"


class ClickHousePins:
    def __init__(self, s: Settings):
        self._reads = ProjectReads(s)  # the client connects lazily: the app is built at import

    def pin(self, project: UUID, trace_ids: list[str]) -> None:
        """Queues one mutation per table; ClickHouse applies them in the background. `traces`
        is mutated too, since a materialized view only sees inserts (ADR-0013)."""
        for table in ("spans", "traces"):
            self._reads.command(
                project,
                f"ALTER TABLE {table} UPDATE retention_days = {PINNED_RETENTION} "
                "WHERE project_id = {project:UUID} "
                "AND trace_id IN {traces:Array(FixedString(32))}",
                {"traces": trace_ids},
            )
