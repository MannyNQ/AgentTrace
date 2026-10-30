# trayce

Python SDK for [Trayce](https://github.com/MannyNQ/Trayce), open-source observability and evals for LLM agents. It traces agent runs into a Trayce server and provides the `trayce` CLI (`trayce eval`, `trayce replay`).

```python
from trayce import instrument_anthropic, trace

instrument_anthropic()  # each Anthropic model call becomes an LLM span


@trace(name="support_agent", kind="agent")
def run(question: str) -> str: ...
```

The SDK reads `TRAYCE_API_KEY` and `TRAYCE_ENDPOINT`. Extras: `trayce[eval]` for `trayce eval`, `trayce[otel]` for the OpenTelemetry bridge.

See the [repository README](https://github.com/MannyNQ/Trayce#readme) for running the server.
