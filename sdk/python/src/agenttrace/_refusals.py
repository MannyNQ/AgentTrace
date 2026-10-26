"""Ingest's refusals as people read them. Ingest answers `{"detail": {"error": ...}}`; these turn
that into a sentence, and say what to do about a key ingest won't take (ADR-0033)."""

import json

SETTINGS = "an owner makes keys on the project's Settings page in the web app"


def reason(body: bytes) -> str:
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        return body[:300].decode(errors="replace")
    if isinstance(detail, dict):
        fields = detail.get("fields")
        error = str(detail.get("error", detail))
        return f"{error}: {fields}" if fields else error
    return str(detail)


def explain(status: int, why: str, key_from: str = "AGENTTRACE_API_KEY") -> str:
    """`why` with what to do next, for the refusals a person can fix by changing the key.
    `key_from` names where the caller's key was set."""
    if status == 401:
        return f"{why}. Check {key_from}; {SETTINGS}"
    if status == 403 and "needs a full API key" in why:
        return (
            f"{why}. `agenttrace eval` and `agenttrace replay` need a full key, while agents in "
            f"production should keep an ingest key; {SETTINGS}"
        )
    return why
