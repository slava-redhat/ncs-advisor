# 6. Treat TSNs as structured knowledge, not blind prose

- **Status:** Proposed
- **Date:** 2026-09-28

## Context

Nokia Technical Support Notes (TSNs, `data/NCS-*/solutions/TSN-*.pdf`) are the
system's memory of *how real NCS issues were actually solved* — the exact thing
the advisor should learn from to handle future issues. They are not free-form:
every TSN follows a fixed template (confirmed from `TSN_NCS_SW_030.pdf` and
`Technical_Support_Note_template.pdf`):

```
1  TSN information (ID, TSN-NCS-SW-NNN)
4  Purpose
5  Validity → 5.1 Impacted system/software release
             5.2 Impacted products
             5.3 Impacted features
6  Keywords
7  Executive summary
8  Network impact
9  Detailed description                         ← symptom + cause
10 Solution and correction instructions         ← the fix
11 Related Jira ID                              ← ticket_id
```

Today the ingest (`ingest/ingest.py`) treats a TSN PDF exactly like a product
manual: split into fixed 1000-char chunks, embed, done. Three problems:

1. **Structure is discarded.** The high-signal "Solution and correction
   instructions" is diluted across generic chunks; a symptom query can retrieve
   the boilerplate and miss the fix.
2. **Boilerplate pollutes every chunk.** The running `DN1000291295 1-0` footer,
   the copyright/no-warranty block, and the table of contents are embedded and
   FTS-indexed as if they were content, degrading both retrieval arms.
3. **Applicability is folder-bound.** A TSN's own "Impacted software release"
   field (which may list 25.7 *and* 25.11) is ignored; the fix only answers for
   the folder it was dropped in ([ADR-0003](0003-version-partitioned-hybrid-rrf-retrieval.md)).

A structured `solutions.csv` path already exists (`symptom/cause/resolution/
ticket_id`) and proves the shape works — TSNs just aren't parsed into it.

## Decision (proposed)

Add a **TSN-aware loader** in ingest that section-splits by the template headings
and emits structured, cleaned chunks reusing the existing solution shape:

- strip boilerplate (copyright block, running `DN… N-N` footer, TOC) before
  embedding;
- map fields to metadata: `tsn_id`, `Related Jira ID → ticket_id`,
  `Impacted software release → version(s)`, `Impacted products/features →
  subsystem/flavor tags`, `Keywords → a keyword field feeding the FTS arm`;
- keep **"Detailed description" (symptom+cause)** and **"Solution and correction
  instructions" (fix)** as distinct chunks, so symptom queries match the
  description and synthesis pulls the exact correction steps;
- honor the TSN's own impacted-release list for cross-version applicability
  instead of the folder alone.

Fall back to the current blind-chunk path for any solution PDF that doesn't match
the template (non-TSN prose, older formats).

## Consequences

- Turns "we have the PDFs" into "the advisor can map a new symptom → the matching
  TSN → its exact correction instructions + Jira ID." This is the concrete
  mechanism for *learning from past solutions to solve future issues*.
- Some flavor/role guardrails in the synthesize prompt
  ([ADR-0004](0004-prompt-grounding-as-the-guardrail.md)) become retrieval-time
  metadata filters — smaller prompt, structural guarantee.
- Cost: one parser plus a template-detection heuristic, and re-ingest. The
  template is stable across TSNs, so the parser is small; it stays best-effort
  with a graceful fallback so a format drift never drops a solution entirely.
- Enables the feedback loop in [IMPROVEMENTS](../IMPROVEMENTS.md): capture
  unresolved sessions → author a new TSN/CSV row → re-ingest → the advisor knows
  it next time.
</content>
