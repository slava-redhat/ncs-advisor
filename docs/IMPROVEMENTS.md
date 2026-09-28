# Improvements & robustness roadmap

Ranked by payoff ÷ effort. Each item says *why*, the *lazy* version, and *when*
to bother. The app is already well-built; nothing here is a rewrite. The theme:
**move the highest-value guardrails from "asked in the prompt" to "enforced in
code", and turn the TSN corpus from prose into structured, learnable knowledge.**

Read alongside the ADRs — several items are the "Consequences" made actionable.

---

## Tier 1 — highest payoff, small diff

### 1. Verify citations in code (close the biggest grounding gap)
**Why.** Every grounding rule lives in the synthesize prompt
([ADR-0004](adr/0004-prompt-grounding-as-the-guardrail.md)); *nothing checks the
output obeyed them*. A fabricated `[7]` when only 6 sources were retrieved ships
silently.
**Lazy version.** After `synthesize`, regex the `[n]` markers in the answer and
assert each `n` ≤ `len(hits)+len(cve_data)`. If any is out of range, drop the
citation and append a "⚠ unverified" note (or re-ask the model once). ~15 lines,
no new deps. This is the single highest-value guardrail.
**When.** Now — it's cheap and it's the failure that most erodes trust.

### 2. Guard the empty-retrieval path
**Why.** If `hits` is empty and there's no `cve_data`, `synthesize` still runs on
"(no matching documentation found)" and can improvise. Prompt rule 4 asks it not
to; a hard guard removes the option.
**Lazy version.** In `route_after_retrieve` (or a tiny check in `synthesize`), if
no grounded context exists, return a fixed "I couldn't find version-specific
sources for this — here's what to collect: …" message and skip the LLM.
**When.** Now — pairs with #1.

### 3. Retries + timeout on the Vertex call
**Why.** One flaky LLM call surfaces as a raw `st.error` and a dead turn
(`app.py` catch-all). Networked LLMs are flaky.
**Lazy version.** Wrap `get_llm().invoke` with `langchain_core`'s built-in
`.with_retry(stop_after_attempt=3)` — no new dep. Confirm `max_tokens=4096`
isn't truncating long answers ([ADR-0005](adr/0005-claude-on-vertex-param-filtering.md));
bump if it is.
**When.** Now — one-liner.

### 4. Strip PDF boilerplate at ingest
**Why.** The running `DN… N-N` footer, copyright/no-warranty block, and TOC are
embedded and FTS-indexed on every page, diluting both retrieval arms
([ADR-0006](adr/0006-tsn-solutions-as-structured-knowledge.md)).
**Lazy version.** A few regexes in `ingest.py`'s page loop to drop the known
footer/copyright/TOC lines before splitting. Re-run `make rag-eval` to confirm
recall holds or improves.
**When.** Now — improves quality for free, no schema change.

---

## Tier 2 — real robustness, moderate effort

### 5. Retrieval relevance-grading node (Corrective-RAG lite)
**Why.** The README already defers "relevance-grade nodes → add when retrieval
quality dips." It's also a *guardrail*: it stops synthesis on weak context
instead of forcing an answer.
**Lazy version.** One structured-output LLM call grading the top-k as
relevant/weak. If all weak → route to `clarify` or the empty-retrieval message
(#2) rather than `synthesize`. Reuse the existing structured-output pattern; no
new framework.
**When.** When answer-quality complaints appear, or right after #1–#2 as their
natural upstream.

### 6. Move CN-A/CN-B and node-role isolation into retrieval filters
**Why.** These are the two longest, most fragile blocks of the synthesize prompt
([ADR-0004](adr/0004-prompt-grounding-as-the-guardrail.md)). A metadata filter is
a *structural* guarantee; a prompt rule is a *persuasive* one.
**Lazy version.** Tag chunks with `flavor` (CN-A/CN-B/both) and, where clean,
`node_role` at ingest — most reliably via the TSN parser (#8). Then filter by the
user's stated `deployment`/`node_roles` in `rag_search_hybrid`. Shrinks the
prompt, hardens the guarantee.
**When.** After #8 lands the metadata; do them together.

### 7. Persist LangGraph checkpoints in Postgres
**Why.** `MemorySaver` is in-process and unbounded
([ADR-0002](adr/0002-in-process-langgraph-stategraph.md)): conversations vanish on
restart and threads accumulate for the process's life — a slow leak for a
long-running or multi-user deployment.
**Lazy version.** Swap `MemorySaver` for `langgraph-checkpoint-postgres` against
the pgvector DB already running. Config-level change, one new dep.
**When.** When the app runs continuously for real users, or memory growth shows.
Skip while it's single-session demo use.

### 8. TSN-aware structured ingest — *learning from past solutions*
**Why + design:** [ADR-0006](adr/0006-tsn-solutions-as-structured-knowledge.md).
This is the core of "learn from TSNs to solve future issues": section-split the
fixed TSN template, strip boilerplate, and emit structured chunks
(`tsn_id`, `ticket_id`, impacted release(s)/products/features, keywords, and a
separated **symptom+cause** vs **solution** pair). Reuses the existing
`solutions.csv` shape — not a new subsystem.
**Payoff.** Symptom → matching TSN → exact correction instructions + Jira ID,
across all versions the TSN declares itself valid for. Also feeds #6's metadata.
**When.** High priority — it's what makes the solution corpus more than PDFs.

---

## Tier 3 — do it before you trust the answers at scale

### 9. Answer-grounding eval to complement the retrieval eval
**Why.** `eval_golden_set.json` measures *retrieval* (right source in top-k),
which is honest and good — but *synthesis* is where the guardrails live and
where a model update will silently regress. Nothing tests the answer today.
**Lazy version.** ~10 checked-in cases with expected assertions ("must cite the
CN-B source, must NOT mention `openstack baremetal`, must not invent a CVE
verdict"). Run the graph, assert with cheap string/citation checks first;
add an LLM-judge only where a string check can't express it. Extends
`eval_rag.py`'s spirit, no new harness.
**When.** Before relying on answers in production, and as a regression gate on
prompt/model changes.

### 10. Observability: LangSmith tracing (env-gated)
**Why.** The graph is a black box beyond Streamlit's status labels. When an
answer is wrong you need to see which chunks were retrieved and what the router
decided.
**Lazy version.** Set `LANGCHAIN_TRACING_V2`/`LANGCHAIN_API_KEY` — LangGraph
emits traces with zero code change. Gate on env so it's off by default.
**When.** The first time you debug a bad answer and wish you had the trace.

### 11. Structure-aware chunking
**Why.** Fixed 1000/150-char splits cut commands, tables, and alarm-code blocks
mid-line — exactly the high-signal tokens ([ADR-0003](adr/0003-version-partitioned-hybrid-rrf-retrieval.md)).
README already flags "fixed→sentence-aware chunk tuning."
**Lazy version.** Try `RecursiveCharacterTextSplitter` tuned to split on headings
/ blank lines / list boundaries, or keep fenced command blocks intact. Gate every
change on `make rag-eval` — don't tune blind.
**When.** When #4 + #8 are in and recall still leaves specific commands unfound.

---

## Explicitly *not* recommended (YAGNI)

- **FastAPI split** — one client (the UI). Add when a second, non-UI client
  actually exists ([ADR-0002](adr/0002-in-process-langgraph-stategraph.md)).
- **Agent/tool-calling rewrite** — the flow is a fixed pipeline; an agent
  re-deciding it every turn buys nothing but nondeterminism.
- **A vector DB other than pgvector** — Postgres already does dense + FTS + the
  metadata filter in one place. Migrating adds ops surface for no measured win.
- **Fast/main LLM tiering** — deferred in the README; add only behind a cheap
  classifier once cost is a measured problem.
- **NVD API key** — add on the first 429, not before (`advisor/cve.py` already
  notes this).

---

## The feedback loop (ties it together)

The point of #8 + #9: make the advisor *improve from use*. When a session ends
unresolved, that's a candidate for a new solution row — capture it (a thumbs-down
in the UI, or just a log), have an engineer author a TSN or a `solutions.csv`
line, `make ingest`, and the advisor knows it next time. The structured TSN path
makes each captured fix immediately retrievable by symptom, with its Jira ID and
version applicability intact. That is how the system "learns from TSNs to solve
future issues" — not a bigger model, a tighter loop between real fixes and the
corpus.
</content>
