# NCS Advisor

Version-aware RAG troubleshooting advisor for Nokia NCS. Ask about an issue on a
specific NCS version (25.7, 25.11, …); it retrieves from that version's product
docs **and** known solutions, and answers with inline citations. Built on
**LangChain + LangGraph** (StateGraph, not a CrewAI/agent swarm), Postgres +
pgvector, local Ollama embeddings, and corporate Claude on Vertex.

## Architecture

```
Streamlit chat (in-process graph)
        │
   LangGraph StateGraph  ── MemorySaver checkpointer (thread per session)
        │
   router ─┬─ ask_version (version missing → ask, end turn)
           └─ retrieve ─┬─ cve_lookup (security only, NVD) ─┐
                        └──────────────────────────────────┴─ synthesize → cited answer
        │
   pgvector (hybrid dense+FTS RRF, version-filtered)  +  host Ollama (embeddings)
```

- **router** (`advisor/nodes.py`) — resolves the NCS version, issue type, a retrieval
  query, and any CVE ids from the whole conversation (structured output). No version
  stated → **ask_version** asks and ends the turn; the next message continues the thread.
- **retrieve** — version-filtered hybrid retrieval (`advisor/retrieval.py`): pgvector
  cosine ∪ Postgres FTS fused by Reciprocal Rank Fusion; `source_type=solution` chunks
  get a small prior so known fixes outrank raw doc prose.
- **cve_lookup** — only for security questions; NVD REST 2.0 by CVE id or keyword.
- **synthesize** — Claude answers from the retrieved sources only, with `[n]` citations.

## Retrieval evaluation

The checked-in `ui/eval_golden_set.json` contains ten source-anchored queries
covering alarms, upgrades, Ceph, storage, worker networking, and the CN-B
worker-upgrade case. Run the evaluation against the running stack with:

```bash
make rag-eval
```

The production retriever is **hybrid RRF**, not MMR. The report includes
`Recall@8` and `MRR@8` for dense vectors, PostgreSQL word/FTS search
(`words`), hybrid RRF, and a diagnostic dense-only MMR rerank. It also reports
what percentage of the hybrid top-8 came from both lists, dense only, or words
only. MMR is diagnostic only and is not used by the production path.

Baseline measured on the ingested GKE corpus on 2026-09-17:

```text
cases=10, k=8, candidate_pool=24, mmr_lambda=0.5
                    Recall@8   MRR@8
dense                 100%      84.17%
words/FTS               50%      43.33%
hybrid RRF            100%      95.00%
diagnostic MMR        100%      86.25%

Hybrid top-8 candidate presence:
both dense+words       15.0%  (12/80)
dense only             83.8%  (67/80)
words only              1.3%  (1/80)
```

The golden set is intentionally source-level: a hit means the expected
ingested source file appears in the top-k, not that an LLM judged the answer.
Re-run after corpus, embedding-model, chunking, or retrieval changes and update
the baseline above only when the command output has been reviewed.

## Data layout

Version = the `NCS-XX-Y` directory (`NCS-25-7` → `25.7`). Drop files, then `make ingest`.

```
data/NCS-25-7/pdfs/*.pdf          # product docs      → source_type=doc
data/NCS-25-7/solutions/*.pdf     # known fixes prose → source_type=solution
data/NCS-25-7/solutions/*.csv     # known fixes rows  → source_type=solution
data/NCS-25-11/…                  # next version, same shape
```

CSV columns: `version, symptom, cause, resolution, ticket_id` (see
`solutions.csv.example`). The folder sets the version unless a row's `version` overrides it.
`make ingest` auto-discovers every `data/NCS-*` version and is incremental (sha256 ledger).

## Run

```bash
cp .env.example .env            # set ANTHROPIC_VERTEX_PROJECT_ID
cp ~/.config/gcloud/application_default_credentials.json ./.application_default_credentials.json
# host Ollama must listen on all interfaces (see .env.example)
make ingest                     # embed all versions on disk
make up                         # UI at http://localhost:8501
make stats                      # chunks by version + source_type
```

## Deferred (ponytail)

- classify + relevance-grade nodes → add when retrieval quality dips
- fast/main LLM tiering → add with a cheap classifier node
- FastAPI split → add when a non-UI client needs the API (`# in-process graph` today)
- NVD API key → add on 429s
- fixed→sentence-aware chunk tuning → revisit on recall
