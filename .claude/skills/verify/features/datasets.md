# Datasets

Datasets live behind `/v1/datasets` in the ingest API (ADR-0019); the web app calls it from the server with `AGENTTRACE_API_KEY`. A dataset has an editable draft and immutable versions, each a list of item revisions (ADR-0020). Users add a trace to a draft with "Add to dataset" (the server copies the root input, from S3 when offloaded), write the expected output or trajectory, and publish. Publishing refuses unchanged drafts and new or changed items without expectations. The version page shows what was added, changed, and removed versus the previous version.

## Sub-features

- `ds-migrate` ingest applies `migrations/NNNN_*.sql` on startup and records them in `schema_migrations`.
- `ds-api` `/v1/datasets` endpoints need a Bearer key and are scoped to its project.
- `ds-create-rename-archive` creates datasets (lowercase slugs, unique), renames them (the id stays), and archives them (hidden from the list and the picker, read-only).
- `ds-add-trace` copies a trace's root input into the draft; a second add is 409 with the existing item's id; truncated, missing, or over-1 MB inputs are refused.
- `ds-item-edit` creates and edits items as JSON; every edit adds a revision; a stale `rev` is 409 and the UI offers a reload.
- `ds-publish` publishes the next version; refuses "nothing to publish" and lists new or changed items without expectations.
- `ds-version-diff` shows added, changed, removed, and unchanged items versus the previous version, with contents as of that version.
- `ds-source-expired` shows "source trace expired" when the linked trace is gone.

## How to get to it (user POV)

- Home page → "Datasets →" → create a dataset → open it. "Rename or archive" sits under the title.
- Any trace page → the picker under the header (an existing dataset, or "New dataset…" plus a name) → "Add to dataset".
- Dataset page → an item's input (edit), "New item", "Publish vN", the versions list → a version page.
- The API directly: `curl -H "Authorization: Bearer $AGENTTRACE_API_KEY" http://127.0.0.1:18000/v1/datasets`.

## Driving it with agenttrace-verify

Preconditions:

- A healthy run. `ANTHROPIC_API_KEY` in `.env` for the demo agent journey.
- The API key the web server and curl send is the one `H` exports (`.env`'s, else `at-local-dev-key`).

- **Migrations.** `docker exec agenttrace-verify-postgres-1 psql -U agenttrace -d agenttrace -tAc "SELECT version, name FROM schema_migrations"` prints `1|0001_datasets.sql`.
- **Integration tests.** `POSTGRES_DSN=postgresql://agenttrace:agenttrace@127.0.0.1:15432/agenttrace CLICKHOUSE_HOST=127.0.0.1 CLICKHOUSE_PORT=18123 S3_ENDPOINT=http://127.0.0.1:18333 REDIS_URL=redis://127.0.0.1:16379/15 uv run pytest services/ingest -m integration` passes. It covers from-trace with an S3-offloaded input, revisions, deletes, and concurrent publishes. It leaves `it-xxxxxxxx` datasets behind.
- **Demo agent → dataset.** `H agent "I want a refund for order 1042, it arrived broken"`, then find the trace with `H ch <name> "SELECT trace_id FROM spans WHERE parent_span_id = '' AND name = 'support_agent' FORMAT TSV"`. On its trace page choose "New dataset…", name it `refund-failures`, and click "Add to dataset": "Added. Fill in what should have happened". Choose `refund-failures` and add again: "this trace is already in the draft" with "Open the item".
- **Publish refused.** On the dataset page, "Publish v1" shows "1 new or changed items have no expected output or expected trajectory".
- **Field errors.** On the item, set Expected trajectory to `[{"name": "lookup_order"}]` and save: `0.tool: Field required` under that field, text kept. Set it to `[{"tool": "lookup_order", "args": {"order_id": "1042"}}, {"tool": "escalate"}]` and save; "Publish v1" then succeeds and the item shows `unchanged`.
- **Stale edit.** Open the item page, then change the item with curl (`PATCH /v1/datasets/<id>/items/<item_id>` with the current `rev`). Save on the page: "the item changed since you loaded it (rev N, now N+1)" and "Reload the item", which loads rev N+1.
- **v2 and the diff.** Add a hand-written item ("New item"), publish v2, open `/datasets/<id>/versions/2`: "Versus v1: 1 added · 1 changed · 0 removed · 0 unchanged"; each row expands to its contents at that version. `GET /v1/datasets/<id>/versions/1` still returns the old revision.
- **Archive.** "Rename or archive" → "Archive dataset": it leaves `/datasets` and the trace page picker; "Show archived" (`/datasets?archived=1`) lists it marked `archived`; its page offers "Unarchive"; `POST .../items` answers 422 "archived". Creating a dataset with the archived one's name says "already exists" and links to it ("Open that dataset").
- **New dataset from an uncopyable trace.** On the `verify.oversized` trace (SDK-truncated input), choose "New dataset…", name it, and add: "the trace's input can't be copied…" or "the SDK cut…", and no dataset of that name appears, even under "Show archived".
- **JSON jsonb can't hold.** `curl -H "Authorization: Bearer $AGENTTRACE_API_KEY" -H 'Content-Type: application/json' -d '{"input": NaN}' http://127.0.0.1:18000/v1/datasets/<id>/items` answers 422 with `fields.input` naming NaN.
- **ClickHouse down.** `H store-stop clickhouse`, then open a dataset page: it renders, with source traces shown as plain links instead of "source trace expired". `H store-start clickhouse` and `H doctor` afterwards.
- **Source expired.** `H ch <name> "ALTER TABLE traces DELETE WHERE trace_id = '<trace_id>' SETTINGS mutations_sync = 1"` stands in for retention: the dataset page shows "source trace expired" for that item, and the item page says the item keeps its copy of the input.

## Gotchas

- Use `find` refs or `form_input` for the item form. A field error adds a line and shifts every field below it, so coordinates from an earlier screenshot hit the wrong textarea.
- React resets a form after its action runs. The forms echo the submitted values back on refusal, so a test reading textarea values after a refusal is checking exactly that.
- Dataset pages are addressed by dataset id (UUID), not by name, because names can change.
- Not-found pages (`/datasets/<unknown id>`) stream a 404 page with HTTP 200, like `/traces/<unknown>`. Check the body, not the status.
- Ingest logs nothing about migrations at uvicorn's default log level. Check `schema_migrations` instead.
