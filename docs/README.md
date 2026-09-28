# NCS Advisor — docs

Design records and the improvement roadmap. The runnable "how do I use it"
lives in the top-level [`README.md`](../README.md); this folder is the *why* and
the *what next*.

## Architecture Decision Records

ADRs capture decisions already baked into the code, so the next person (or the
next you) doesn't re-litigate them. Format: Status · Context · Decision ·
Consequences. One decision per file, append-only — supersede, don't rewrite.

| ADR | Decision | Status |
|-----|----------|--------|
| [0001](adr/0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](adr/0002-in-process-langgraph-stategraph.md) | In-process LangGraph StateGraph, not an agent swarm or a service | Accepted |
| [0003](adr/0003-version-partitioned-hybrid-rrf-retrieval.md) | Version-partitioned hybrid (dense + FTS) RRF retrieval | Accepted |
| [0004](adr/0004-prompt-grounding-as-the-guardrail.md) | Grounding lives in the synthesize prompt | Accepted (with known gaps) |
| [0005](adr/0005-claude-on-vertex-param-filtering.md) | Claude on Vertex via a param-filtering ChatAnthropic subclass | Accepted |
| [0006](adr/0006-tsn-solutions-as-structured-knowledge.md) | Treat TSNs as structured knowledge, not blind prose | Proposed |

## Roadmap

- [IMPROVEMENTS.md](IMPROVEMENTS.md) — robustness, LangChain/LangGraph
  guardrails, learning from TSNs, and better ways, ranked by payoff vs. effort.
</content>
</invoke>
