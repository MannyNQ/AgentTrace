# Web health page

The public `/status` page shows whether the stack is up; it needs no session and shows only what ingest's `/health` shows (`/` now redirects to sign-in or a project). The static shell renders immediately, and the service health list streams in per request: one row per store with a green dot and `ok`, a red dot and the store's real error, or a single "Ingest API unreachable" notice when the API itself is down.

## Sub-features

- `page-ok` shows four rows (Postgres, ClickHouse, Redis, Object storage), each `ok` with a green dot.
- `page-store-error` shows the stopped store's real error in red while the other rows stay `ok`.
- `page-ingest-unreachable` replaces the list with "Ingest API unreachable" and a `docker compose up -d` hint.
- `page-fresh-per-request` shows the current state on every load, never a cached one.

## How to get to it (user POV)

- Open `http://localhost:3000/status` (dev) or `http://127.0.0.1:13000/status` (verify instance) in a browser, or follow "Service status" on a project's home page.

## Driving it with trayce-verify

Preconditions:

- `H doctor` reports `DOCTOR OK`.
- The built-in browser pane is available for screenshots.

- **All ok, as text.** Run `H page page-ok`. `page-ok.summary` has exactly four lines, each ending in `=> ok`: `Postgres · orgs, projects, datasets`, `ClickHouse · spans`, `Redis · ingest queue`, `Object storage · large payloads`.
- **All ok, visually.** In the browser pane, navigate to `http://127.0.0.1:13000/status`, run `find` for `Service health` (a heading), then `screenshot`. It shows the `Status` title and four green rows. Copy the screenshot into the run dir as `web-page-ok.jpg`.
- **Store error.** Run `H store-stop redis`, then `H page page-redis-down`. The summary shows `Redis · ingest queue => error: ConnectionError: …` and the other three `=> ok`. Reload in the browser pane and take a screenshot: the Redis row has a red dot and red error text. Save it as `web-page-redis-down.jpg`. Then run `H store-start redis`.
- **Fresh per request.** Right after `store-start`, run `H page page-recovered`. All four rows are `=> ok` again on the next load, with no restart of the web server.
- **Ingest unreachable.** Run `H store-stop ingest`, then `H page page-ingest-down`. The summary is `Ingest API unreachable`. A browser screenshot shows the notice with the `docker compose up -d` hint. Save it as `web-page-ingest-down.jpg`. Then run `H store-start ingest` and `H doctor`.

## Gotchas

- In a hidden browser pane the page can sit on "Checking services…" forever, because React reveals streamed content on an animation frame. A `screenshot` forces a frame, and `H page` reads the full server response. Neither is an app bug.
- `get_page_text` right after `navigate` may return only the shell. Use `H page` for text assertions.
- The summary parser matches rows shaped `<p>{label}</p><p class=…>{result}</p>`. If the page markup changes, update `cmd_page` in the helper, never the assertion.
- With ingest stopped, the page waits up to 5 s for the fetch timeout before showing the notice. Connection refused usually returns immediately.
