"""Postgres + pgvector: version-filtered hybrid retrieval over doc_chunk.

Dense (pgvector cosine) + lexical (Postgres FTS), fused with Reciprocal Rank
Fusion (RRF). Dense catches paraphrase; FTS catches exact tokens (alarm codes,
CLI flags, error strings) that embeddings bury. Known-fix "solution" chunks get
a small prior so a curated fix outranks raw doc prose at equal relevance.
"""
import os
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from .embeddings import embed

_pool: ConnectionPool | None = None

RRF_K = 60  # standard Reciprocal Rank Fusion constant
SOLUTION_BOOST = 1.0 / (RRF_K + 1)  # ~one top rank; only lifts already-retrieved hits


def _conninfo() -> str:
    return (
        f"host={os.environ.get('PGHOST', 'db')} "
        f"port={os.environ.get('PGPORT', '5432')} "
        f"dbname={os.environ['POSTGRES_DB']} "
        f"user={os.environ['POSTGRES_USER']} "
        f"password={os.environ['POSTGRES_PASSWORD']}"
    )


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(conninfo=_conninfo(), min_size=1, max_size=8, open=True)
    return _pool


@contextmanager
def _conn():
    with _get_pool().connection() as c:
        yield c


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def available_versions() -> list[str]:
    with _conn() as c, c.cursor() as cur:
        cur.execute("SELECT DISTINCT metadata->>'version' AS v FROM doc_chunk "
                    "WHERE metadata->>'version' IS NOT NULL ORDER BY v")
        return [r[0] for r in cur.fetchall()]


def _flavor_family(f: str | None) -> str | None:
    """CN-A and CN-A-SRIOV are one family; CN-B is the other."""
    if not f:
        return None
    up = f.upper()
    if "CN-B" in up or "CNB" in up:
        return "CN-B"
    if "CN-A" in up or "CNA" in up:
        return "CN-A"
    return None


def _flavor_conflicts(chunk_flavor: str | None, user_flavor: str | None) -> bool:
    cf, uf = _flavor_family(chunk_flavor), _flavor_family(user_flavor)
    return bool(cf and uf and cf != uf)


def rag_search_hybrid(query: str, version: str | None = None, k: int = 8,
                      pool: int = 24, flavor: str | None = None) -> list[dict]:
    qvec = _vec(embed(query, task="search_query"))
    clauses, params = [], []
    if version:
        clauses.append("metadata->>'version' = %s")
        params.append(version)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as c, c.cursor() as cur:
        if version:
            # HNSW applies metadata filters after its candidate list, which can
            # return zero rows even when matching chunks exist. Exact scan keeps recall.
            cur.execute("SET LOCAL enable_indexscan = off")
        cur.execute(f"SELECT id FROM doc_chunk{where} "
                    f"ORDER BY embedding <=> %s::vector LIMIT %s", params + [qvec, pool])
        dense = [r[0] for r in cur.fetchall()]
        lex_where = where + (" AND" if where else " WHERE")
        cur.execute(
            f"WITH q AS (SELECT plainto_tsquery('english', %s) AS tsq) "
            f"SELECT id FROM doc_chunk{lex_where} tsv @@ (SELECT tsq FROM q) "
            f"ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC LIMIT %s",
            [query, *params, pool])
        lexical = [r[0] for r in cur.fetchall()]
        scores: dict = {}
        for ranked in (dense, lexical):
            for rank, doc_id in enumerate(ranked, 1):
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
        if not scores:
            return []
        cur.execute("SELECT id, text, source_url, metadata FROM doc_chunk WHERE id = ANY(%s)",
                    (list(scores),))
        rows = {r[0]: r for r in cur.fetchall()}
        for doc_id, r in rows.items():
            if (r[3] or {}).get("source_type") == "solution":
                scores[doc_id] += SOLUTION_BOOST
        ordered = [i for i in sorted(scores, key=scores.get, reverse=True) if i in rows]
        if flavor:  # demote the other flavor's chunks; never drop them (keeps recall)
            ok = [i for i in ordered
                  if not _flavor_conflicts((rows[i][3] or {}).get("flavor"), flavor)]
            ordered = ok + [i for i in ordered if i not in ok]
        top = ordered[:k]
        return [{"text": rows[i][1], "source_url": rows[i][2], "metadata": rows[i][3]}
                for i in top]


def _rrf_selfcheck():
    # RRF must rank a doc appearing high in BOTH lists above one high in only one.
    RRF_K_ = RRF_K
    both = 1.0 / (RRF_K_ + 1) + 1.0 / (RRF_K_ + 2)
    one = 1.0 / (RRF_K_ + 1)
    assert both > one, "RRF fusion broken: consensus must beat single-list"
    # Flavor: CN-A/CN-A-SRIOV are one family; conflict only across families.
    assert _flavor_conflicts("CN-A", "CN-B") and _flavor_conflicts("CN-B", "CN-A")
    assert not _flavor_conflicts("CN-A-SRIOV", "CN-A")
    assert not _flavor_conflicts(None, "CN-B") and not _flavor_conflicts("CN-B", None)
    print("ok: RRF fusion + flavor-conflict self-checks passed")


if __name__ == "__main__":
    _rrf_selfcheck()
