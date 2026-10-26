# 20. Versions are lists of item revisions; publishing needs expectations on what changed

Status: accepted. Builds on ADR-0005.

## Context

ADR-0005 gives each dataset one editable draft and immutable published versions. Each version must record every item's contents at that point, and an item's `item_id` must stay the same across versions so results can be compared item by item. Most items don't change between versions. Publishing also has to refuse drafts that can't be tested.

## Decision

- **Revisions.** An item's contents (`input`, `expected_output`, `expected_trajectory`, `metadata`) live in `item_revisions(item_id, rev)`. Every edit of a draft item adds a revision; revisions are never changed. The draft is each live item's current revision. `source_trace_id` is fixed when an item is created, so it lives on the item, where a partial unique index allows one draft item per trace.
- **Versions.** A version is a list of `(item_id, rev)` pointers in `version_items`. An unchanged item points at the same revision in consecutive versions, so nothing is copied.
- **Deletes.** Deleting a draft item drops it from the next version. An item that no version points at is deleted outright; otherwise it's only marked deleted.
- **Stale edits.** Every edit and delete names the revision it was based on; a stale revision gets 409.
- **Publishing** runs under a row lock on the dataset and is refused when:
  - the draft equals the latest version ("nothing to publish"),
  - the draft is empty, or
  - a new or changed item has neither `expected_output` nor `expected_trajectory`. The refusal lists those items.

## Consequences

- A version's diff against the previous one is a comparison of two pointer lists: added, removed, changed (different `rev`), unchanged.
- Storage grows by one revision per edit, not by the dataset size per publish.
- An unchanged item published before this rule existed can't block a publish, because only new or changed items are checked.
- An item added from a trace can't be published until someone writes down what should have happened, which is the point of ADR-0001.
- Two concurrent publishes can't both take `v3`: the row lock serializes them, and the `(dataset_id, version)` primary key backs it up.
