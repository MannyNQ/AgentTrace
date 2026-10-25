import dataclasses
from typing import Any

from pydantic import BaseModel


def to_jsonable(value: Any, _depth: int = 0) -> Any:
    """Convert span inputs and outputs to JSON-safe values. Anything unknown becomes its repr."""
    if _depth > 32:
        return repr(value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, BaseModel):
        try:
            return value.model_dump(mode="json", exclude_none=True)
        except Exception:
            return repr(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: to_jsonable(getattr(value, f.name), _depth + 1)
            for f in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v, _depth + 1) for v in value]
    return repr(value)
