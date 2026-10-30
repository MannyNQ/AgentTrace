# Changelog

Each `## X.Y.Z` section is the body of that version's GitHub Release (`.github/workflows/release.yml`).

## 0.1.1

Fixes from installing 0.1.0 on a clean Mac and Ubuntu 24.04.

- `trayce up` checks the ports the stack publishes before starting. When one is taken it names the port, its setting (`TRAYCE_INGEST_PORT` or `TRAYCE_WEB_PORT`), and the `.env` to change, instead of Docker's "port is already allocated". A running Trayce stack of its own is left to compose.
- The README installs the `trayce` command with `pipx` or `uv tool`, since Ubuntu's, Debian's, and Homebrew's Python refuse a bare `pip install`, and adds a first-trace example for the SDK.
- The setup page says the token is printed by `trayce up`.

## 0.1.0

The first release. Alpha: the data model and APIs may change between 0.x releases.

### Install

- `pip install trayce && trayce up` runs the stack with Docker as the only requirement. It writes random store passwords, an API key, and a setup token to `~/.trayce/.env` on the first run, and prints the web UI's address and the SDK and OTLP settings. `trayce down` stops it; `--delete-data` also removes its volumes (ADR-0043).
- Images `ghcr.io/mannynq/trayce-{ingest,worker,web}` for linux/amd64 and linux/arm64, tagged with the package version.
- Ingest applies Postgres and ClickHouse migrations on startup.

### Tracing

- Python SDK with spans for LLM calls, tool calls, agent runs, and retrievals, and an Anthropic client integration. LLM spans record the full request and response (ADR-0039).
- OTLP/HTTP endpoint at `/otlp/v1/traces`. Reads OpenTelemetry GenAI conventions and OpenInference; other spans are kept as `custom` spans (ADR-0035).
- `pip install 'trayce[otel]'` bridges OpenTelemetry spans into the SDK in-process (ADR-0036).
- Cost is computed once at ingestion from the price in effect at the span's start, with cache reads and writes priced separately. A model with no price gets a null cost, not 0 (ADR-0010).
- Large payloads are offloaded to object storage (ADR-0009). Traces expire after a retention period unless pinned (ADR-0011).

### Web UI

- Trace list and span tree for each trace.
- Project overview for the last 24 hours, 7 days, or 30 days: volume, error rates, latency percentiles, spend by model, and tool usage, each chart linking to its traces (ADR-0037).
- Sign-in, owner and member roles, projects, and per-project API keys with `ingest` or `full` scope (ADR-0032, ADR-0033). The first account claims the install with a setup token (ADR-0034).

### Datasets and evals

- Any trace can be added to a dataset. Datasets have a draft and published, immutable versions (ADR-0005, ADR-0020).
- `trayce eval` runs your agent over a published version client-side and uploads an experiment (ADR-0006, ADR-0023). Scorers: `exact_match`, `contains`, `regex`, `json_schema`, `trajectory` (tool calls and their order), `custom`, and `llm_judge` with an Anthropic model and binary verdicts (ADR-0027).
- Each experiment is compared with its baseline: the run at the merge-base, else the newest on the base branch (ADR-0007, ADR-0026). A regression must be significant under a paired item-level bootstrap and exceed a threshold (ADR-0008, ADR-0025).
- `trayce eval --fail-on-regression` and the GitHub Action (`uses: MannyNQ/Trayce@v1`) fail a pull request on a regression and post the comparison as one comment per `eval.yaml`.

### Replay

- `trayce replay <trace_id>` re-runs a trace's input with your current code and scores the runs against the dataset item made from it (ADR-0028, ADR-0030). `--from <span_id>` replays a stubbed prefix and forks at that span; `--stub-tools` answers every tool call from the recording (ADR-0040).

### Known limitations

- Docker is required.
- Only Python has an SDK; other languages send traces over OTLP.
- `llm_judge` supports Anthropic models only.
