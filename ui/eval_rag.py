"""Evaluate retrieval recall against the checked-in golden set.

Run inside the UI container, where PostgreSQL and the Ollama embedding service
are available:

    python eval_rag.py

The production retriever is hybrid RRF, not MMR. This script reports both the
production path and a diagnostic dense-only MMR rerank for comparison.
"""
import argparse
import json
import math
from collections import Counter
from pathlib import Path

from advisor import retrieval
from advisor.embeddings import embed


DEFAULT_GOLDEN = Path(__file__).with_name("eval_golden_set.json")
RRF_K = retrieval.RRF_K


def _vector(value):
    return [float(x) for x in value.strip("[]").split(",") if x]


def _lists(query, version, pool):
    qvec = embed(query, task="search_query")
    qvec_text = retrieval._vec(qvec)
    clauses = ["metadata->>'version' = %s"]
    params = [version]
    where = " WHERE " + " AND ".join(clauses)
    with retrieval._conn() as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL enable_indexscan = off")
        cur.execute(
            f"SELECT id FROM doc_chunk{where} "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            params + [qvec_text, pool],
        )
        dense = [row[0] for row in cur.fetchall()]
        lexical_where = where + " AND"
        cur.execute(
            f"WITH q AS (SELECT plainto_tsquery('english', %s) AS tsq) "
            f"SELECT id FROM doc_chunk{lexical_where} tsv @@ (SELECT tsq FROM q) "
            f"ORDER BY ts_rank_cd(tsv, (SELECT tsq FROM q)) DESC LIMIT %s",
            [query, *params, pool],
        )
        lexical = [row[0] for row in cur.fetchall()]
        ids = list(dict.fromkeys(dense + lexical))
        if not ids:
            return qvec, dense, lexical, {}, {}
        cur.execute(
            "SELECT id, metadata, embedding::text FROM doc_chunk WHERE id = ANY(%s)",
            (ids,),
        )
        rows = {row[0]: row for row in cur.fetchall()}
    return qvec, dense, lexical, rows, _rrf_order(dense, lexical, rows)


def _rrf_order(dense, lexical, rows):
    scores = {}
    for ranked in (dense, lexical):
        for rank, doc_id in enumerate(ranked, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    for doc_id, row in rows.items():
        if (row[1] or {}).get("source_type") == "solution":
            scores[doc_id] += retrieval.SOLUTION_BOOST
    return sorted(scores, key=scores.get, reverse=True)


def _cosine(left, right):
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(x * x for x in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def _mmr_order(query_vec, dense, rows, k, lambda_mult):
    selected = []
    remaining = list(dense)
    doc_vectors = {doc_id: _vector(rows[doc_id][2]) for doc_id in dense if doc_id in rows}
    while remaining and len(selected) < k:
        best_id = max(
            remaining,
            key=lambda doc_id: (
                lambda_mult * _cosine(query_vec, doc_vectors[doc_id])
                - (1 - lambda_mult) * max(
                    (_cosine(doc_vectors[doc_id], doc_vectors[other])
                     for other in selected),
                    default=0.0,
                )
            ),
        )
        selected.append(best_id)
        remaining.remove(best_id)
    return selected


def _matches(source, expected):
    return any(fragment in source for fragment in expected)


def _rank(order, rows, expected, k):
    for position, doc_id in enumerate(order[:k], 1):
        source = (rows.get(doc_id) or ({}, {},))[1].get("source", "")
        if _matches(source, expected):
            return position
    return None


def _metric(ranks):
    hits = [rank for rank in ranks if rank is not None]
    return {
        "recall": len(hits) / len(ranks) if ranks else 0.0,
        "mrr": sum(1 / rank for rank in hits) / len(ranks) if ranks else 0.0,
    }


def evaluate(golden, k=8, pool=24, lambda_mult=0.5):
    ranks = {name: [] for name in ("dense", "words", "hybrid", "mmr")}
    mix = Counter()
    gold_mix = Counter()
    details = []
    for case in golden:
        qvec, dense, words, rows, hybrid = _lists(
            case["query"], case["version"], pool
        )
        mmr = _mmr_order(qvec, dense, rows, k, lambda_mult)
        expected = case["expected_sources"]
        case_ranks = {
            "dense": _rank(dense, rows, expected, k),
            "words": _rank(words, rows, expected, k),
            "hybrid": _rank(hybrid, rows, expected, k),
            "mmr": _rank(mmr, rows, expected, k),
        }
        for name, rank in case_ranks.items():
            ranks[name].append(rank)

        for doc_id in hybrid[:k]:
            in_dense = doc_id in dense
            in_words = doc_id in words
            mix["both" if in_dense and in_words else
                "dense_only" if in_dense else "words_only"] += 1
        hybrid_rank = case_ranks["hybrid"]
        if hybrid_rank is not None:
            source_id = next(
                doc_id for doc_id in hybrid[:k]
                if _matches(rows[doc_id][1].get("source", ""), expected)
            )
            in_dense = source_id in dense[:pool]
            in_words = source_id in words[:pool]
            gold_mix["both" if in_dense and in_words else
                      "dense_only" if in_dense else "words_only"] += 1
        details.append({"id": case["id"], "ranks": case_ranks})

    return {
        "cases": len(golden),
        "k": k,
        "pool": pool,
        "mmr_lambda": lambda_mult,
        "metrics": {name: _metric(values) for name, values in ranks.items()},
        "top_k_candidate_mix": dict(mix),
        "hybrid_gold_hit_mix": dict(gold_mix),
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--pool", type=int, default=24)
    parser.add_argument("--mmr-lambda", type=float, default=0.5)
    args = parser.parse_args()
    golden = json.loads(Path(args.golden).read_text())
    print(json.dumps(evaluate(golden, args.k, args.pool, args.mmr_lambda), indent=2))


if __name__ == "__main__":
    main()
