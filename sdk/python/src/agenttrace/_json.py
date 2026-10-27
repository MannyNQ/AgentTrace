import dataclasses
import math
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


def is_lossless(value: Any, _depth: int = 0) -> bool:
    """Whether `to_jsonable(value)` is `value` itself: plain JSON types all the way down, so the
    recorded copy can stand in for it (ADR-0040). A tuple, a dataclass, a model, or a non-finite
    float would come back as something else."""
    if _depth > 32:
        return False
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if type(value) is list:
        return all(is_lossless(v, _depth + 1) for v in value)
    if type(value) is dict:
        return all(isinstance(k, str) and is_lossless(v, _depth + 1) for k, v in value.items())
    return False
