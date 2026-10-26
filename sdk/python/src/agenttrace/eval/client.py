"""The runner's view of the ingest API: datasets to read, and `/v1/experiments` to write
(ADR-0019, ADR-0023). Every write is idempotent, so a failed request is retried as is."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agenttrace.eval.results import Finish, NewExperiment, SampleResult


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


def _detail(body: bytes) -> str:
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        return body[:300].decode(errors="replace")
    if isinstance(detail, dict):
        fields = detail.get("fields")
        error = str(detail.get("error", detail))
        return f"{error}: {fields}" if fields else error
    return str(detail)


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
                with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                    text = r.read()
                return json.loads(text) if text else None
            except urllib.error.HTTPError as e:
                reason = _detail(e.read())
                if e.code < 500:
                    raise ApiError(f"{method} {path}: {e.code} {reason}", e.code) from e
                error = f"{e.code} {reason}"
            except (urllib.error.URLError, OSError) as e:
                error = str(getattr(e, "reason", e))
        raise ApiError(f"{method} {path}: couldn't reach {self.base} ({error})")

    def dataset_version(self, dataset: str, version: int | str) -> DatasetVersion:
        """`dataset` is a name or an id; `version` a number or "latest". Archived datasets can
        still be run, since their versions stay readable."""
        try:
            UUID(dataset)
            found = self.request("GET", f"/v1/datasets/{dataset}")
        except ValueError:
            listed = self.request("GET", "/v1/datasets?include_archived=true")["datasets"]
            found = next((d for d in listed if d["name"] == dataset), None)
            if found is None:
                raise ApiError(f'no dataset named "{dataset}" in this project') from None
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
