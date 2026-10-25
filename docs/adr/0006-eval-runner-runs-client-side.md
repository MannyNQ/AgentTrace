# 6. The eval runner runs on the client, never the server

Status: accepted

## Context

Running an experiment means executing the user's agent, with their code, tools, tool credentials, and provider API keys.

## Decision

The `agenttrace` CLI is the eval runner. It imports the agent entry point named in `eval.yaml`, runs it over each item with the SDK tracing every sample, runs the scorers (including custom Python scorers), and uploads results. The server stores and compares results and scorer configs, and never executes user code.

## Consequences

- The server never holds user code, tool credentials, or provider keys for evals.
- Experiments run wherever the user runs the CLI: locally or in GitHub Actions.
- Server-side "run this experiment" buttons are out of scope; the UI can only show results.
