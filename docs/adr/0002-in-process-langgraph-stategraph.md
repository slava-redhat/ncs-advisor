# 2. In-process LangGraph StateGraph, not an agent swarm or a service

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

The advisor triages an NCS support question, gathers missing context, retrieves
from version-scoped docs, optionally looks up CVE data, and writes a cited
answer. That is a *fixed pipeline with a couple of branches*, not an open-ended
task needing autonomous tool-choosing agents.

Options considered:

1. A CrewAI / ReAct multi-agent swarm that decides its own steps.
2. A LangGraph `StateGraph` — an explicit graph of nodes and conditional edges.
3. A FastAPI service fronting the graph, with Streamlit as a thin client.

## Decision

Use a **LangGraph `StateGraph`, compiled once and run in-process inside the
Streamlit app** (`advisor/graph.py`, `@lru_cache(maxsize=1)` singleton). The
flow is explicit:

```
router ─┬─ ask_version            (no version stated → ask, end turn)
        ├─ clarify                (version known, request too vague → ask once)
        └─ retrieve ─┬─ cve_lookup (security only)
                     └────────────┴─ synthesize → cited answer
```

State is a `TypedDict` (`advisor/state.py`); conversation is checkpointed per
Streamlit session via `MemorySaver` keyed by a `thread_id`.

No agent swarm: the control flow is known, so encode it as edges — deterministic,
debuggable, cheap. No FastAPI split: there is one client (the UI), so a network
hop and a second deployable would be pure overhead today.

## Consequences

- Control flow is readable and testable; each node is a plain function.
- Deterministic routing beats an agent re-deciding the plan every turn.
- **`MemorySaver` is in-process and unbounded**: conversations are lost on
  restart and every thread lives for the life of the process. Fine for a single
  UI; a memory leak for a long-running multi-user deployment. Swap to
  `langgraph-checkpoint-postgres` when sessions must survive restarts or scale
  out. (See [IMPROVEMENTS](../IMPROVEMENTS.md).)
- No API for non-UI clients yet. The graph is import-clean, so a FastAPI wrapper
  is a small, additive change when a second client appears — deliberately
  deferred, not precluded.
- The router does five jobs in one structured-output call (version, issue type,
  symptom query, env facts, sufficiency). Cheap and coherent; if any one job
  degrades, split it into its own node (see deferred "classify + relevance-grade"
  in the top-level README).
</content>
