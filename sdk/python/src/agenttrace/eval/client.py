"""The runner's view of the ingest API: datasets and traces to read, and `/v1/experiments` and
`/v1/replays` to write (ADR-0019, ADR-0023, ADR-0029). Every write is idempotent, so a failed
request is retried as is."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agenttrace import _refusals as refusals
from agenttrace._quiet import quiet
from agenttrace.eval.results import (
    Finish,
    NewExperiment,
    NewReplay,
    ReplayRunResult,
    SampleResult,
)


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class VersionItem:
    item_id: str
    rev: int
    contents: dict[str, Any]


@dataclass(frozen=True)
class DatasetVersion:
    dataset_id: str
    name: str
    version: int
    items: list[VersionItem]


class Client:
    def __init__(self, endpoint: str, api_key: str, timeout_s: float = 30.0, retries: int = 3):
        self.base = endpoint.rstrip("/")
        self.api_key, self.timeout_s, self.retries = api_key, timeout_s, retries

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """JSON in and out. Network errors and 5xx are retried with backoff; a 4xx is the
        caller's mistake and raises ApiError with the server's reason."""
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        error = ""
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(0.5 * 2 ** (attempt - 1))
            try:
                with quiet(), urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    text = r.read()
                return json.loads(text) if text else None
            except urllib.error.HTTPError as e:
                why = refusals.reason(e.read())
                if e.code < 500:
                    raise ApiError(
                        f"{method} {path}: {e.code} {refusals.explain(e.code, why)}", e.code
                    ) from e
                error = f"{e.code} {why}"
            except (urllib.error.URLError, OSError) as e:
                error = str(getattr(e, "reason", e))
        raise ApiError(f"{method} {path}: couldn't reach {self.base} ({error})")

    def project(self) -> dict[str, Any]:
        """The key's project, whose slug the web app's links need (design.md, Phase 6)."""
        return self.request("GET", "/v1/project")

    def dataset(self, dataset: str) -> dict[str, Any]:
        """`dataset` is a name or an id. Archived datasets are found too."""
        try:
            UUID(dataset)
            return self.request("GET", f"/v1/datasets/{dataset}")
        except ValueError:
            listed = self.request("GET", "/v1/datasets?include_archived=true")["datasets"]
            found = next((d for d in listed if d["name"] == dataset), None)
            if found is None:
                raise ApiError(f'no dataset named "{dataset}" in this project') from None
            return found

    def dataset_version(self, dataset: str, version: int | str) -> DatasetVersion:
        """`dataset` is a name or an id; `version` a number or "latest". Archived datasets can
        still be run, since their versions stay readable."""
        found = self.dataset(dataset)
        if version == "latest":
            if found["latest_version"] is None:
                raise ApiError(f'dataset "{found["name"]}" has no published version; publish one')
            version = found["latest_version"]
        v = self.request("GET", f"/v1/datasets/{found['id']}/versions/{version}")
        items = [VersionItem(i["item_id"], i["rev"], i["contents"]) for i in v["items"]]
        return DatasetVersion(found["id"], found["name"], v["version"], items)

    # Fields left at their defaults aren't sent, so a field added in a later phase (judge usage,
    # merge-base, max_drop) only reaches the server when it's used, and an older ingest that
    # refuses unknown fields still accepts everything else. The server's defaults are the same.

    def create_experiment(self, e: NewExperiment) -> dict[str, Any]:
        return self.request(
            "POST", "/v1/experiments", e.model_dump(mode="json", exclude_defaults=True)
        )

    def upload_samples(self, experiment_id: str, samples: list[SampleResult]) -> None:
        body = {"samples": [s.model_dump(mode="json", exclude_defaults=True) for s in samples]}
        self.request("POST", f"/v1/experiments/{experiment_id}/samples", body)

    def finish(self, experiment_id: str, finish: Finish) -> dict[str, Any]:
        path = f"/v1/experiments/{experiment_id}/finish"
        return self.request("POST", path, finish.model_dump(mode="json"))

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/experiments/{urllib.parse.quote(experiment_id)}")

    def comparison(self, experiment_id: str) -> dict[str, Any]:
        """Against the baseline ingest selects (ADR-0024)."""
        path = f"/v1/experiments/{urllib.parse.quote(experiment_id)}/comparison"
        return self.request("GET", path)

    def draft_item_for_trace(self, dataset_id: str, trace_id: str) -> VersionItem | None:
        """The dataset's draft item made from `trace_id`, at its current revision. A trace is in
        a dataset's draft at most once."""
        items = self.request("GET", f"/v1/datasets/{dataset_id}/items")["items"]
        found = next((i for i in items if i["source_trace_id"] == trace_id), None)
        return (
            None
            if found is None
            else VersionItem(found["item_id"], found["rev"], found["contents"])
        )

    def trace_input(self, trace_id: str) -> dict[str, Any]:
        """The trace's root span and its full input, and the original it re-ran if it's a
        replay run (ADR-0028)."""
        return self.request("GET", f"/v1/traces/{urllib.parse.quote(trace_id)}/input")

    def trace_steps(self, trace_id: str) -> list[dict[str, Any]]:
        """The trace's LLM and tool spans in order, with full payloads, for a fork (ADR-0040)."""
        path = f"/v1/traces/{urllib.parse.quote(trace_id)}/steps"
        return self.request("GET", path)["steps"]

    def create_replay(self, r: NewReplay) -> dict[str, Any]:
        return self.request("POST", "/v1/replays", r.model_dump(mode="json", exclude_defaults=True))

    def upload_replay_runs(self, replay_id: str, runs: list[ReplayRunResult]) -> None:
        body = {"runs": [r.model_dump(mode="json", exclude_defaults=True) for r in runs]}
        self.request("POST", f"/v1/replays/{replay_id}/runs", body)

    def finish_replay(self, replay_id: str, finish: Finish) -> dict[str, Any]:
        return self.request(
            "POST", f"/v1/replays/{replay_id}/finish", finish.model_dump(mode="json")
        )

    def replay(self, replay_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/replays/{urllib.parse.quote(replay_id)}")
