---
artifact_type: raw
source_slug: ag2-captain-agent
source_url: fixture:ag2-captain-agent-concept
source_title: Captain Agent - AG2 Multi-Agent Reference Architecture
source_authors: AG2 Team
source_publisher: AG2 Docs
source_published_at: 2026-05-15
fetched_at: 2026-06-26T00:00:00Z
fetched_via: fixture
fixture_kind: concept_positive
fixture_domain: AI Agent
---

# Captain Agent — AG2 Multi-Agent Reference Architecture

## Overview

The Captain Agent pattern in AG2 (AutoGen 2) provides a reference architecture for
multi-agent task delegation with sub-agent lifecycle management. Unlike single-agent
loops where one model handles all reasoning, the Captain pattern separates a
coordinating agent (the "Captain") from a pool of specialized execution agents
that it dispatches tasks to and collects results from.

## Architectural Components

### 1. Captain (Coordinator Agent)

The Captain agent holds the system-level goal and maintains a task graph. It does
not execute domain work directly. Instead, it:

- Decomposes the incoming goal into sub-tasks
- Selects an appropriate executor from the agent pool based on capability metadata
- Dispatches the sub-task with a scoped context (not the full conversation history)
- Collects the executor's result and integrates it into the task graph
- Decides whether to dispatch a follow-up task or declare completion

### 2. Executor Pool

Each executor agent registers a capability manifest (e.g., "code-generation",
"web-search", "data-analysis"). The Captain matches sub-task requirements to
executor capabilities. Executors are stateless between dispatches — they receive
a scoped prompt, produce a result, and return control. This isolation prevents
one executor's context from polluting another's.

### 3. Task Graph and Checkpointing

The task graph is a DAG of sub-tasks with dependencies. Each node records:
- The executor that handled it
- The scoped context that was passed
- The result returned
- A status (pending, in-progress, completed, failed)

Checkpointing serializes the task graph after each node completion. If the
Captain crashes mid-orchestration, a new Captain instance can resume from the
last checkpoint rather than restarting the entire goal.

### 4. Sub-Agent Lifecycle

Executors are spawned on demand and terminated after producing a result. The
lifecycle is:

1. **Spawn**: Captain selects an executor template and instantiates it with
   the scoped context
2. **Execute**: The executor runs its reasoning loop (which may itself be a
   single-agent loop with tool calls)
3. **Return**: The executor returns a structured result (not free text — a
   typed response object)
4. **Terminate**: The executor process is cleaned up; its context is not
   persisted

This contrasts with persistent multi-agent systems where all agents maintain
long-running conversation state. The Captain pattern trades continuity for
isolation: each sub-task gets a clean slate, reducing context bleed.

## Delegation Protocol

The Captain communicates with executors via a typed message protocol:

```
DispatchMessage:
  task_id: str
  executor_capability: str
  scoped_context: dict  # only the slice of context this task needs
  constraints: dict     # max_tokens, timeout, allowed_tools

ResultMessage:
  task_id: str
  status: "completed" | "failed"
  result: Any           # typed response object
  error: str | None
  tokens_used: int
```

The scoped_context is critical: it prevents an executor from seeing the full
conversation history. The Captain decides what context is relevant to each
sub-task. This is a form of context engineering at the orchestration layer.

## Comparison with Alternatives

| Pattern | Coordination | Executor State | Isolation |
|---------|-------------|----------------|-----------|
| Captain Agent | Centralized (Captain) | Stateless per-task | High |
| Peer-to-Peer Network | Decentralized (message passing) | Stateful | Low |
| Pipeline (Sequential) | Fixed order | Stateful | Medium |
| Hierarchical (Tree) | Tree-structured | Mixed | Medium |

The Captain pattern's strength is isolation: because executors are stateless
and receive scoped context, a failure in one executor does not cascade. The
weakness is overhead: the Captain must reason about decomposition and dispatch
for every sub-task, which adds latency and token cost.

## When to Use the Captain Pattern

The Captain pattern is well-suited for:

- Goals that decompose into independent sub-tasks (e.g., "research topic X,
  write a summary, and generate a presentation" — three independent tasks)
- Environments where executor isolation is important (e.g., untrusted code
  execution where one agent's failure should not affect others)
- Workflows where the decomposition is not known in advance (the Captain
  decides dynamically based on intermediate results)

It is less suited for:

- Tightly coupled tasks where executors need to share intermediate state
- Low-latency workflows where the dispatch overhead is unacceptable
- Simple goals that a single agent can handle in one pass

## Key Takeaways

The Captain Agent pattern provides a structured approach to multi-agent
orchestration with strong isolation guarantees. By separating coordination
(Captain) from execution (executor pool) and enforcing scoped context per
dispatch, it reduces context bleed and limits failure propagation. The
trade-off is increased coordination overhead and the need for a capable
coordinator agent that can reason about task decomposition.
