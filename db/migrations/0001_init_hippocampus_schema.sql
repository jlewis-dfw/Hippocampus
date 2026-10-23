-- HIPPO-E01-F1-S02: hippocampus schema (specs.md section 4).
-- Embedding dimension is locked to bge-small-en-v1.5 (384).
CREATE EXTENSION IF NOT EXISTS "vector";

CREATE SCHEMA IF NOT EXISTS hippocampus;

CREATE TABLE hippocampus.collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE hippocampus.sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID NOT NULL REFERENCES hippocampus.collections(id) ON DELETE CASCADE,
    uri VARCHAR(1024) NOT NULL,
    title VARCHAR(512),
    sha256_hash CHAR(64) NOT NULL,
    chunk_count INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'indexed',
    error_message TEXT,
    last_indexed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_collection_uri UNIQUE (collection_id, uri)
);

CREATE TABLE hippocampus.document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES hippocampus.sources(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    header_path VARCHAR(512),
    content TEXT NOT NULL,
    token_count INT NOT NULL,
    embedding vector(384) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_chunk UNIQUE (source_id, chunk_index)
);

CREATE INDEX idx_document_chunks_embedding_hnsw
ON hippocampus.document_chunks
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_sources_collection ON hippocampus.sources(collection_id);
CREATE INDEX idx_chunks_source ON hippocampus.document_chunks(source_id);
