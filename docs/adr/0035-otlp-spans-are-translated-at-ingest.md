# 35. OTLP spans are translated into the span contract at ingest

Status: accepted. Builds on ADR-0017 and ADR-0033.

## Context

Only the Python SDK can send spans, and its one integration is the Anthropic client. Agents built on the Vercel AI SDK, Pydantic AI, Google ADK, Strands, or Mastra already emit OpenTelemetry spans, and LangChain, LlamaIndex, and the OpenAI Agents SDK do through OpenInference instrumentors. ADR-0017 left OTLP for a later phase.

Two conventions cover them. OpenTelemetry's GenAI conventions (`gen_ai.*`) are still at Development status and renaming attributes; OpenInference (`openinference.span.kind`, `llm.*`, `input.value`) is stable. Both count cached tokens inside input tokens, where our `input_tokens` excludes them (ADR-0010). The senders are third-party code we don't control, and OTel exporters don't surface a rejected batch to anyone.

## Decision

- **Endpoint.** `POST /otlp/v1/traces` takes OTLP/HTTP, protobuf or JSON, gzipped or not, and answers in the request's encoding. Users set `OTEL_EXPORTER_OTLP_ENDPOINT` to `<ingest>/otlp`; the prefix keeps it apart from `GET /v1/traces`. No gRPC. The existing 5 MB and 1000-span limits apply, the size after decompression.
- **Auth.** `Authorization: Bearer <key>` from `OTEL_EXPORTER_OTLP_HEADERS`; an `ingest` key is enough, and the key decides the project. Resource attributes can't name one.
- **One storage path.** Ingest translates each OTel span into an `agenttrace.Span` and pushes it onto the same Redis stream. Offloading, cost, dedup, and trace completion don't change, and OTel IDs are already our IDs (ADR-0003). The translator lives in the `agenttrace` package beside the span contract.
- **Lenient, per span.** The whole request is refused only when it can't be decoded or breaks a limit (400), or the key is bad (401 or 403). A span with a bad or all-zero trace or span ID, or no start time, is dropped and counted in `partial_success.rejected_spans`. Unknown attributes are never a reason to reject. When Redis can't be reached the answer is 503 with `Retry-After`, which OTLP exporters retry.
- **Conventions.** OpenInference when a span sets `openinference.span.kind`, else GenAI, else none; the result is recorded in `agenttrace.convention`. Old and new GenAI names are both read (`gen_ai.system` and `gen_ai.provider.name`, `cache_creation` and `cache_write`). Vendor namespaces (`traceloop.*`, `claude_code.*`, ...) aren't mapped. Kinds:

  | Kind | GenAI `gen_ai.operation.name` | OpenInference |
  |---|---|---|
  | `llm` | `chat`, `text_completion`, `generate_content`, `embeddings` | LLM, EMBEDDING |
  | `tool` | `execute_tool` | TOOL |
  | `agent` | `invoke_agent`, `create_agent`, `invoke_workflow`, `plan` | AGENT |
  | `retrieval` | `retrieval` | RETRIEVER, RERANKER |
  | `custom` | anything else, or none | CHAIN, GUARDRAIL, EVALUATOR, PROMPT, DECISION |

  Spans with no convention, such as HTTP and database spans, are kept as `custom`.
- **Tokens and cost.** `input_tokens` is the reported input minus cache reads and writes, floored at 0. Every cache write counts as `cache_write_5m_tokens`. The model is `gen_ai.response.model`, else `gen_ai.request.model` or `llm.model_name`, priced by exact name as today.
- **Content.** Messages (`gen_ai.input.messages` with `gen_ai.system_instructions`, `gen_ai.output.messages`, or `input.value` and `output.value`) and tool arguments and results (`gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`) become the span's input and output, parsed as JSON where they are JSON, and leave `attributes`. Other attribute values are cut to 4 KB. Content in log events is ignored.
- **Trace metadata** comes from the root span's own attributes only: `gen_ai.conversation.id` or `session.id`, `user.id` or `enduser.id`, `tag.tags`, and OpenInference's `metadata`. Resource attributes are copied onto root spans with a `resource.` prefix.
- **Status.** `ERROR` is `error`; `UNSET` and `OK` are `ok`. `error_type` is `error.type`, else the first `exception` event's `exception.type`.
- **Remote parents.** A span flagged `SPAN_FLAGS_CONTEXT_IS_REMOTE` is stored as a root.

## Consequences

- Strictness differs by sender: `/v1/spans` still refuses a whole batch for one bad span, because there it's our SDK's bug (ADR-0017); OTLP drops what it can't store and keeps the rest.
- GenAI content capture is off by default (`OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT`). Without it, tool spans have no arguments and trajectory scorers that match `args` fail; the setup docs say to turn it on.
- 1-hour cache writes are priced as 5-minute ones, a slight undercount, until a convention splits them.
- A dated model name the price table doesn't hold gives a null cost, as for any unpriced model.
- A downstream service's spans form their own trace. If the upstream service sends to the same project, the two stay separate traces instead of one.
- Metadata set only on child spans stays in those spans' attributes; nothing is lifted to the root across batches.
- The GenAI conventions will keep renaming attributes; the mapping table is the one place those names live.
