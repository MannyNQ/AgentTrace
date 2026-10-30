# 1. Differentiate on trajectory evals and failures-become-tests

Status: accepted

## Context

Langfuse, LangSmith, Phoenix, Braintrust, and Helicone already cover general LLM tracing and evals. Trayce can't out-feature them and needs a sharp angle.

## Decision

Trayce's angle is the loop from a production failure to a CI regression test that grades the agent's steps:

- **Trajectory evals**: first-class scoring of which tools the agent called, in what order, with what arguments, in how many steps.
- **Failures become tests**: one click turns a failed trace into a dataset item, including its trajectory.

Local-first mode, MCP-specific tracing, and other angles are out of scope for v1.

## Consequences

- Dataset items must be able to carry an expected trajectory, and must link back to their source trace.
- Tool spans must be reliably distinguishable, which is one reason span kinds are a closed set.
- Polish goes to the trace → dataset → CI path before dashboards or replay.
