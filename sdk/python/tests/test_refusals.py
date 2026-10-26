"""Ingest's refusals reach people as sentences that say what to do about the key (ADR-0033),
and the CLI's links go into the key's project (design.md, Phase 6)."""

import logging

import pytest
from test_exporter import make_span

from agenttrace import _refusals as refusals
from agenttrace.cli import _project_url
from agenttrace.eval.client import ApiError, Client
from agenttrace.eval.replay import summary
from agenttrace.exporter import Config, Exporter

SCOPE = {"detail": {"error": "this needs a full API key; this one's scope is ingest"}}
UNKNOWN = {"detail": {"error": "unknown or revoked API key"}}


def test_a_refusal_is_its_error_text():
    assert refusals.reason(b'{"detail": {"error": "nope"}}') == "nope"
    assert (
        refusals.reason(b'{"detail": {"error": "bad", "fields": {"x": "y"}}}') == "bad: {'x': 'y'}"
    )
    assert refusals.reason(b"<html>proxy error</html>") == "<html>proxy error</html>"


def test_a_key_refusal_says_what_to_do():
    assert "Check AGENTTRACE_API_KEY" in refusals.explain(401, "unknown or revoked API key")
    scope = refusals.explain(403, SCOPE["detail"]["error"])
    assert "need a full key" in scope and "Settings" in scope
    assert refusals.explain(403, "only an owner can do this") == "only an owner can do this"
    assert refusals.explain(422, "bad") == "bad"


def test_the_cli_names_the_scope_it_needs(http_server):
    client = Client(http_server(lambda p, h, b: (403, SCOPE)), "ingest-key", retries=0)
    with pytest.raises(ApiError) as e:
        client.request("POST", "/v1/experiments", {})
    assert e.value.status == 403
    assert "this one's scope is ingest" in str(e.value) and "need a full key" in str(e.value)


def test_the_exporter_logs_the_reason_not_json(http_server, caplog):
    ex = Exporter(
        Config(
            api_key="gone",
            endpoint=http_server(lambda p, h, b: (401, UNKNOWN)),
            flush_interval_s=60,
        )
    )
    with caplog.at_level(logging.WARNING, logger="agenttrace"):
        ex.export(make_span())
        ex.flush()
    assert "unknown or revoked API key. Check AGENTTRACE_API_KEY" in caplog.text
    assert "agenttrace.configure()" in caplog.text
    assert '{"detail"' not in caplog.text


def test_the_exporter_cuts_a_long_refusal(http_server, caplog):
    fields = {
        "detail": {
            "error": "some fields are invalid",
            "fields": {str(i): "x" * 50 for i in range(1000)},
        }
    }
    ex = Exporter(
        Config(
            api_key="k", endpoint=http_server(lambda p, h, b: (422, fields)), flush_interval_s=60
        )
    )
    with caplog.at_level(logging.WARNING, logger="agenttrace"):
        ex.export(make_span())
        ex.flush()
    assert "some fields are invalid" in caplog.text
    assert len(caplog.text) < 1000


class Projects:
    def __init__(self, answer):
        self.answer = answer

    def project(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_links_go_into_the_keys_project():
    assert _project_url(Projects({"slug": "support-bot"}), "https://at.example/") == (
        "https://at.example/p/support-bot"
    )


def test_without_a_project_answer_links_use_the_bare_address():
    old_ingest = Projects(ApiError("GET /v1/project: 404 Not Found", 404))
    assert _project_url(old_ingest, "https://at.example") == "https://at.example"
    assert _project_url(Projects({"slug": "x"}), None) is None


def test_the_replay_summary_links_into_the_project():
    detail = {
        "id": "r1",
        "status": "completed",
        "runs_uploaded": 3,
        "cost_usd": 0.0,
        "unpriced_runs": 0,
        "item_id": None,
        "errored": 0,
        "pass_counts": [],
    }
    assert "https://at.example/p/default/replays/r1" in summary(
        detail, "https://at.example/p/default"
    )
