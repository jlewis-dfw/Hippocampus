-- Saved web sources: a start URL with the crawl settings (depth, pattern, engine) that fed a collection, so they can be
-- shown, changed and re-run. Pages remember the depth they were found at and the web source that found them, so a
-- lower depth can remove the deeper pages. Pages indexed before this migration have no depth and are never pruned.
CREATE TABLE hippocampus.web_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID NOT NULL REFERENCES hippocampus.collections(id) ON DELETE CASCADE,
    url VARCHAR(1024) NOT NULL,
    depth SMALLINT NOT NULL DEFAULT 2 CONSTRAINT ck_web_sources_depth CHECK (depth BETWEEN 1 AND 5),
    match_pattern TEXT,
    engine VARCHAR(16) NOT NULL DEFAULT 'static' CONSTRAINT ck_web_sources_engine CHECK (engine IN ('static', 'browser')),
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at TIMESTAMPTZ,
    last_status VARCHAR(16) CONSTRAINT ck_web_sources_status CHECK (last_status IN ('ok', 'error')),
    last_summary JSONB,                         -- counts of the last run, or {"error": "..."}; never page contents
    CONSTRAINT uq_web_sources_url UNIQUE (collection_id, url)
);

CREATE INDEX idx_web_sources_collection ON hippocampus.web_sources(collection_id);

ALTER TABLE hippocampus.sources
    ADD COLUMN crawl_depth SMALLINT,
    ADD COLUMN web_source_id UUID REFERENCES hippocampus.web_sources(id) ON DELETE SET NULL;

CREATE INDEX idx_sources_web_source ON hippocampus.sources(web_source_id) WHERE web_source_id IS NOT NULL;
