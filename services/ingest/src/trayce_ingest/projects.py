"""Projects: listing them, creating them with a slug that never changes, and renaming them or
changing their retention (architecture.md, Phase 6). Only owners create and change projects; storage
is the auth `Store`."""

import re
import unicodedata
from uuid import UUID

from trayce_ingest.auth import Owner, Project, ProjectOwner, Store, User
from trayce_ingest.errors import NotFound, Refused

MAX_SLUG_CHARS = 40
MIN_RETENTION_DAYS, MAX_RETENTION_DAYS = 1, 3650
MAX_SLUG_TRIES = 50


def slugify(name: str) -> str:
    """`Support Bot (EU)` -> `support-bot-eu`. Accents fold to ASCII; anything else becomes a
    hyphen; a name with no letters or digits becomes `project`."""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return slug[:MAX_SLUG_CHARS].rstrip("-") or "project"


def _name(name: str) -> str:
    name = name.strip()
    if not name:
        raise Refused("a project needs a name")
    return name


def _retention(days: int) -> int:
    if not MIN_RETENTION_DAYS <= days <= MAX_RETENTION_DAYS:
        raise Refused(f"retention_days must be from {MIN_RETENTION_DAYS} to {MAX_RETENTION_DAYS}")
    return days


def list_projects(store: Store, user: User) -> list[Project]:
    return store.projects(user.org_id)


def get(store: Store, project_id: UUID) -> Project:
    project = store.project_by_id(project_id)
    if project is None:
        raise NotFound("project not found")
    return project


def create(store: Store, owner: Owner, name: str) -> Project:
    """The slug comes from the name, with `-2`, `-3`, ... added until it's free in the org."""
    name, base = _name(name), slugify(name)
    for n in range(1, MAX_SLUG_TRIES + 1):
        suffix = "" if n == 1 else f"-{n}"
        slug = base[: MAX_SLUG_CHARS - len(suffix)].rstrip("-") + suffix
        project = store.create_project(owner.user.org_id, name, slug)
        if project is not None:
            return project
    raise Refused(f'too many projects have slugs like "{base}"; pick a more distinct name')


def update(store: Store, by: ProjectOwner, name: str | None, retention_days: int | None) -> Project:
    """A new `retention_days` applies to spans ingested from then on; stored spans keep the
    retention stamped on them (ADR-0016)."""
    return store.update_project(
        by.project_id,
        None if name is None else _name(name),
        None if retention_days is None else _retention(retention_days),
    )
