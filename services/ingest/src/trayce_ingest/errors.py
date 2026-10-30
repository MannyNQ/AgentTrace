"""The refusals every rule raises and every route answers as `{"detail": {"error": ..., ...}}`
with the error's `status` (`http.install_error_handlers`)."""

from typing import Any, ClassVar
from uuid import UUID


class ApiError(Exception):
    """A refusal the API returns as `{"detail": {"error": ..., ...}}` with `status`."""

    status: ClassVar[int] = 422

    def __init__(
        self,
        error: str,
        *,
        items: list[UUID] | None = None,
        item_id: UUID | None = None,
        dataset_id: UUID | None = None,
        rev: int | None = None,
    ):
        super().__init__(error)
        self.error, self.items, self.rev = error, items or [], rev
        self.item_id, self.dataset_id = item_id, dataset_id

    def detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": self.error}
        if self.items:
            detail["items"] = [str(i) for i in self.items]
        if self.item_id is not None:
            detail["item_id"] = str(self.item_id)
        if self.dataset_id is not None:
            detail["dataset_id"] = str(self.dataset_id)
        if self.rev is not None:
            detail["rev"] = self.rev
        return detail


class NotFound(ApiError):
    status = 404


class Conflict(ApiError):
    status = 409


class Refused(ApiError):
    """Well-formed, but breaks a rule."""

    status = 422
