# 1. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

NCS Advisor is a running product with several non-obvious choices already made
(in-process graph, hybrid retrieval, prompt-only grounding, a param-filtering
Vertex client). These decisions are currently only legible by reading the code
and its comments. New contributors — and future-us — need to know *why* before
changing *what*.

## Decision

Keep short ADRs in `docs/adr/`, numbered and append-only. One decision per file.
When a decision changes, add a new ADR that supersedes the old one rather than
editing history. Keep them boring and factual; the code is the source of truth,
the ADR is the rationale.

Scope: architecture and cross-cutting choices only. Do not ADR every helper —
that is what code comments are for, and this codebase already comments its
tricky bits well (see `advisor/llm.py`, `advisor/retrieval.py`).

## Consequences

- A reviewer can see intent without archaeology.
- One more file to touch when a real decision changes. Acceptable.
- Format is [MADR](https://adr.github.io/madr/)-lite: Status, Context, Decision,
  Consequences. No tooling required.
</content>
