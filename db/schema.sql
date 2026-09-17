-- NCS Advisor: single pgvector DB for versioned RAG over NCS docs + solutions.
CREATE EXTENSION IF NOT EXISTS vector;

-- RAG corpus chunks (embeddings + provenance).
-- metadata: {version, source_type, title, source, page, ticket_id}
--   version     e.g. "25.7" | "25.11"   (the authoritative retrieval filter)
--   source_type "doc" (product PDFs) | "solution" (known fixes: pdf/csv rows)
CREATE TABLE IF NOT EXISTS doc_chunk (
    id          BIGSERIAL PRIMARY KEY,
    text        TEXT NOT NULL,
    source_url  TEXT,
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   vector(768) NOT NULL
);
CREATE INDEX IF NOT EXISTS doc_chunk_embedding_idx
    ON doc_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunk_version_idx
    ON doc_chunk ((metadata->>'version'));
-- Lexical half of hybrid retrieval: a generated tsvector so exact tokens
-- (alarm codes, CLI flags, error strings) that dense embeddings bury stay
-- findable. Fused with the vector score via RRF in retrieval.py.
ALTER TABLE doc_chunk ADD COLUMN IF NOT EXISTS tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
CREATE INDEX IF NOT EXISTS doc_chunk_tsv_idx ON doc_chunk USING GIN (tsv);

-- Ingest ledger: what has been loaded + content hash for idempotent re-ingest.
CREATE TABLE IF NOT EXISTS ingested_source (
    source      TEXT PRIMARY KEY,   -- "<version>/<filename>"
    kind        TEXT NOT NULL,      -- doc|solution
    sha256      TEXT NOT NULL,      -- hash of source bytes; unchanged => skip
    chunks      INT  NOT NULL DEFAULT 0,
    ingested_at TIMESTAMPTZ DEFAULT now()
);
