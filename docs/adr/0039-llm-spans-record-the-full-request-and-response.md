# 39. LLM spans record the full request and response

Status: accepted. Builds on ADR-0009; makes forks (ADR-0040) possible.

## Context

An `anthropic.messages.create` span records `system` and `messages` as input, a few request parameters as stringified attributes, tools by name only (`llm.tools`), and `response.content` as output. A fork has to hand the agent the response it got at each prefix step and notice when today's code makes a different request, and neither works from that: there is no response id, stop reason, or usage to rebuild a `Message` from, and no tool schemas or real parameter values to compare against.

## Decision

- The input is the call's full `create` keyword arguments: `model`, `system`, `messages`, `tools` with their schemas, `tool_choice`, `temperature`, `max_tokens`, `thinking`, `betas`, and the rest. Transport options (`extra_headers`, `extra_query`, `extra_body`, `timeout`) are left out.
- The output is the full response: `id`, `model`, `stop_reason`, `usage`, and `content`.
- The stringified parameter attributes are dropped. `llm.tools` stays, for search.
- Large payloads are offloaded as any other (ADR-0009).

## Consequences

- LLM payloads grow, mostly from tool schemas repeated on every call; more of them are offloaded.
- Traces recorded before this change keep the old shape. They can be replayed whole but not forked: their LLM steps aren't stubbable.
- OTel-bridged and OTLP LLM spans keep whatever their convention records; they aren't stubbable either.
