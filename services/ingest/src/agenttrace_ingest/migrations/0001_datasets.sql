-- ADR-0005, ADR-0020: datasets with one editable draft and immutable versions. A version is a
-- list of item revisions; unchanged items share their revision with the previous version.
CREATE TABLE datasets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES projects(id),
    name         TEXT NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,63}$'),
    description  TEXT NOT NULL DEFAULT '',
    archived_at  TIMESTAMPTZ,              -- archived, never deleted
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, name)
);

CREATE TABLE dataset_items (
    item_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id       UUID NOT NULL REFERENCES datasets(id),
    current_rev      INT NOT NULL,          -- the draft's revision
    source_trace_id  TEXT CHECK (source_trace_id ~ '^[0-9a-f]{32}$'),  -- fixed at creation
    deleted_at       TIMESTAMPTZ,           -- dropped from the draft; versions may still point here
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX dataset_items_draft ON dataset_items (dataset_id, created_at, item_id)
    WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX dataset_items_one_per_trace ON dataset_items (dataset_id, source_trace_id)
    WHERE deleted_at IS NULL AND source_trace_id IS NOT NULL;

CREATE TABLE item_revisions (
    item_id              UUID NOT NULL REFERENCES dataset_items(item_id) ON DELETE CASCADE,
    rev                  INT NOT NULL CHECK (rev >= 1),
    input                JSONB NOT NULL,
    expected_output      JSONB,
    expected_trajectory  JSONB,
    metadata             JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, rev)
);

CREATE TABLE dataset_versions (
    dataset_id    UUID NOT NULL REFERENCES datasets(id),
    version       INT NOT NULL CHECK (version >= 1),
    item_count    INT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset_id, version)
);

CREATE TABLE version_items (
    dataset_id  UUID NOT NULL,
    version     INT NOT NULL,
    item_id     UUID NOT NULL,
    rev         INT NOT NULL,
    PRIMARY KEY (dataset_id, version, item_id),
    FOREIGN KEY (dataset_id, version) REFERENCES dataset_versions (dataset_id, version),
    -- No cascade: an item a version points at can't be deleted (ADR-0020).
    FOREIGN KEY (item_id, rev) REFERENCES item_revisions (item_id, rev)
);
CREATE INDEX version_items_by_item ON version_items (item_id);
