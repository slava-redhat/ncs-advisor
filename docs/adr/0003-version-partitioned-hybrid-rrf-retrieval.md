# 3. Version-partitioned hybrid (dense + FTS) RRF retrieval

- **Status:** Accepted
- **Date:** 2026-09-28

## Context

NCS ships multiple releases (25.7, 25.11, …) whose procedures diverge. An answer
grounded in the wrong version's docs is worse than no answer. Separately, NCS
troubleshooting hinges on *exact tokens* — alarm codes (`CKEY`/`CALM`/`CVLK`),
node states (`wait-call-back`, `deploy-failed`), CLI flags, `/opt/bcmt/log`
paths — that pure vector similarity blurs, while paraphrased symptom descriptions
need semantic matching that keyword search misses.

## Decision

**Partition by version, then fuse two retrievers.** (`advisor/retrieval.py`,
`db/schema.sql`.)

- **Version is the authoritative filter.** `metadata->>'version'` gates every
  query; the router refuses to retrieve until the user states a version
  (`ask_version`). One corpus, filtered — not one DB per version.
- **Hybrid = pgvector cosine (dense) ∪ Postgres FTS (lexical), fused by
  Reciprocal Rank Fusion** (`RRF_K = 60`). Dense catches paraphrase; FTS catches
  the exact tokens embeddings bury.
- **Solution prior:** `source_type=solution` chunks get a small RRF boost
  (`1/(RRF_K+1)`) so a curated known-fix outranks raw doc prose at equal
  relevance — enough to lift, not enough to invent.
- **Exact scan under a version filter:** `SET LOCAL enable_indexscan = off`
  before the dense query. HNSW applies metadata filters *after* building its
  candidate list, which can return zero rows even when matching chunks exist;
  the exact scan preserves recall. The corpus is small enough that this is fine.
- **MMR is diagnostic only**, reported by `eval_rag.py` for comparison, never on
  the production path.

Retrieval quality is guarded by a checked-in, source-level golden set
(`ui/eval_golden_set.json`, `make rag-eval`): a hit means the expected source
file appears in top-k. Baseline (2026-09-17): hybrid `Recall@8 100%`,
`MRR@8 95%`, beating dense-only and FTS-only.

## Consequences

- Wrong-version answers are structurally prevented, not just discouraged.
- Alarm codes and paraphrased symptoms both retrieve well; the numbers back it.
- `enable_indexscan = off` trades index speed for recall correctness. Correct at
  the current corpus size; revisit if the corpus grows enough that the exact scan
  is slow (then fix HNSW filtering properly, don't just re-enable the index).
- The golden set measures *retrieval*, not *answer* quality — synthesis
  correctness is unguarded by tests. See [ADR-0004](0004-prompt-grounding-as-the-guardrail.md)
  and [IMPROVEMENTS](../IMPROVEMENTS.md).
- Cross-version applicability is lost: a fix valid for 25.7 *and* 25.11 is only
  found under the folder it was dropped in. [ADR-0006](0006-tsn-solutions-as-structured-knowledge.md)
  addresses this via TSN "impacted release" metadata.
</content>
