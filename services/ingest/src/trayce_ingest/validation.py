"""Checks for values bound for Postgres, shared by every request body: `jsonb` holds standard
JSON only, and no TEXT or `jsonb` value can hold a NUL character."""

import json
from typing import Annotated, Any

from pydantic import AfterValidator

from trayce_ingest.errors import Refused


def _has_nul(v: Any) -> bool:
    if isinstance(v, str):
        return "\x00" in v
    if isinstance(v, dict):
        return any(_has_nul(k) or _has_nul(x) for k, x in v.items())
    if isinstance(v, list):
        return any(_has_nul(x) for x in v)
    return False


def storable(v: Any, field: str, max_bytes: int | None = None) -> Any:
    """Postgres `jsonb` holds standard JSON only: no NaN or Infinity, and no NUL characters."""
    try:
        text = json.dumps(v, ensure_ascii=False, allow_nan=False)
    except ValueError as e:
        raise ValueError(f"{field} has NaN or Infinity, which JSON can't hold") from e
    if _has_nul(v):
        raise ValueError(f"{field} has a NUL character (\\u0000), which Postgres can't store")
    if max_bytes is not None and len(text.encode()) > max_bytes:
        size = len(text.encode())
        raise ValueError(f"{field} is {size} bytes of JSON; the limit is {max_bytes}")
    return v


def _no_nul(v: str) -> str:
    if "\x00" in v:
        raise ValueError("has a NUL character (\\u0000), which Postgres can't store")
    return v


Text = Annotated[str, AfterValidator(_no_nul)]  # free text stored in a Postgres TEXT column


def check_text(value: str | None, field: str) -> None:
    """For text in a model this service doesn't own (the SDK's wire models), checked by a rule
    rather than by a `Text` field."""
    if value is not None and "\x00" in value:
        raise Refused(f"{field} has a NUL character (\\u0000), which Postgres can't store")
