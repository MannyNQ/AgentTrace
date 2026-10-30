# 42. The product is named Trayce

Status: accepted. Replaces the name AgentTrace everywhere except verbatim captures in `site/proof/v1/`.

## Context

The project shipped as AgentTrace, with the CLI, import, and planned PyPI distribution all `agenttrace`. Before publishing, we checked the name and found four other projects in the same category already using it:

- `agenttrace` on PyPI, from TensorStax (github.com/tensorstax/agenttrace).
- `agenttrace-sdk`, from agenttrace.io, a live product sold as AI agent observability and debugging.
- `agenttrace-py`, from agenttrace.com, deterministic replay.
- `agenttrace-ai`.

We couldn't take the PyPI name, and a search for the product would land on any of four neighbors doing nearly the same thing. The brand toolkit already held back every `pip install` line because of this.

## Decision

- The product is **Trayce**: one word, capital T. Checked on 2026-10-29: `trayce` is free on PyPI and npm, and github.com/MannyNQ/Trayce is free.
- The PyPI distribution, the Python import, and the CLI are all `trayce`. The distribution isn't published yet.
- Environment variables use the `TRAYCE_` prefix, HTTP headers the `Trayce-` prefix, and the Compose project is `trayce` (`trayce-verify` for the verify harness). API keys are minted with a `tr_` prefix, and the published dev key is `tr-local-dev-key`.
- The GitHub repository becomes MannyNQ/Trayce.
- The rename is mechanical and total, with one exception: the captures in `site/proof/` are kept as recorded and still say AgentTrace. The landing page shows them with only the name changed, and `site/proof/README.md` says so. On 2026-10-30 the page's captures were re-recorded on Trayce 0.1.1, so only the first proof in `site/proof/v1/` still says AgentTrace.

## Consequences

- Two nearby names remain. The Trayce API client at trayce.dev (evanrolfe/trayce_gui) is an HTTP and network traffic tool for Docker: same spelling, different product, and it owns trayce.dev. Traycer is an AI coding tool. Copy must never link to trayce.dev or write "Traycer" (docs/brand/toolkit.md, section 8).
- At launch the landing page (`site/`) is served from GitHub Pages at https://mannynq.github.io/Trayce/, deployed by `.github/workflows/pages.yml`. A custom domain is deferred; gettrayce.com is the candidate. trayce.dev is taken, and trayce.com is parked and listed as possibly for sale.
- Existing installs need their `AGENTTRACE_*` variables renamed to `TRAYCE_*`, and a dev stack started under the old Compose project name keeps its volumes under that name until it's taken down.
- Until `trayce` is published to PyPI, the install path is still cloning the repo, and no copy prints a `pip install trayce` line.
