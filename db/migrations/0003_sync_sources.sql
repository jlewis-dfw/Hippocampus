-- Saved sync sources: a named folder (or, later, bucket or repository) that is kept in step with a collection.
-- No credentials are stored here. The rules live in `settings`; a root-folder to collection mapping in `folder_collections`.
CREATE TABLE hippocampus.sync_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,         -- the label in each file's source: files://<name>/<path>
    kind VARCHAR(16) NOT NULL DEFAULT 'upload'
        CONSTRAINT ck_sync_sources_kind CHECK (kind IN ('upload', 'mount', 's3', 'drive', 'git')),
    collection_id UUID NOT NULL REFERENCES hippocampus.collections(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}',      -- include, exclude, extensions, default_excludes, gitignore, folders
    folder_collections JSONB NOT NULL DEFAULT '{}', -- root folder -> collection slug, when each folder has its own
    created_by VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_run_at TIMESTAMPTZ,
    last_status VARCHAR(16)
        CONSTRAINT ck_sync_sources_status CHECK (last_status IN ('ok', 'error')),
    last_summary JSONB                          -- counts of the last run, or {"error": "..."}; never file contents
);

CREATE INDEX idx_sync_sources_collection ON hippocampus.sync_sources(collection_id);
