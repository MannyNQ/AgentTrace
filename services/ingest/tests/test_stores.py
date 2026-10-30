"""The ClickHouse and object-storage reads keep to one project (#32)."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from trayce_ingest.settings import Settings
from trayce_ingest.stores import ProjectReads, read_payload

PROJECT = UUID("00000000-0000-0000-0000-0000000000aa")
OTHER = UUID("00000000-0000-0000-0000-0000000000bb")


class Client:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def query(self, sql: str, parameters: dict):
        self.calls.append((sql, parameters))
        return SimpleNamespace(result_rows=[("row",)])

    def command(self, sql: str, parameters: dict):
        self.calls.append((sql, parameters))


def reads() -> tuple[ProjectReads, Client]:
    r, client = ProjectReads(Settings()), Client()
    r.__dict__["client"] = client  # what the cached property would build
    return r, client


def test_every_read_binds_the_project():
    r, client = reads()
    sql = "SELECT 1 FROM traces WHERE project_id = {project:UUID}"
    assert r.query(PROJECT, sql, {"x": 1}) == [("row",)]
    assert client.calls == [(sql, {"x": 1, "project": str(PROJECT)})]


def test_a_read_that_forgets_the_project_is_refused():
    r, client = reads()
    with pytest.raises(ValueError, match="project_id"):
        r.query(PROJECT, "SELECT 1 FROM traces WHERE trace_id = {t:String}", {"t": "x"})
    with pytest.raises(ValueError, match="project_id"):
        r.command(PROJECT, "ALTER TABLE spans UPDATE retention_days = 0 WHERE 1", {})
    assert client.calls == []


def test_a_caller_can_t_override_the_bound_project():
    r, client = reads()
    sql = "SELECT 1 FROM traces WHERE project_id = {project:UUID}"
    r.query(PROJECT, sql, {"project": str(OTHER)})
    assert client.calls[0][1]["project"] == str(PROJECT)


def test_a_payload_is_read_only_from_its_project_s_prefix():
    s = Settings()
    got: list[str] = []
    store = SimpleNamespace(
        get_object=lambda Bucket, Key: (
            got.append(Key) or {"Body": SimpleNamespace(read=lambda: b'"x"')}
        )
    )
    uri = f"s3://{s.s3_bucket}/{PROJECT}/{'a' * 32}/{'b' * 16}"
    assert read_payload(s, store, PROJECT, uri) == '"x"'
    with pytest.raises(RuntimeError, match="project"):
        read_payload(s, store, OTHER, uri)
    assert got == [f"{PROJECT}/{'a' * 32}/{'b' * 16}"]
