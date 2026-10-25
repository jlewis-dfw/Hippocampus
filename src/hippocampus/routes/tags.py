"""The groups shown in the interactive API reference (``/docs``, and the API tab of the dashboard)."""
HEALTH, SEARCH, COLLECTIONS = "Status", "Search", "Collections"
ACCESS, INGEST, UPLOADS = "Access", "Ingest", "Folder uploads"
SOURCES, DOCS, JOBS = "Saved sources", "Docs sync", "Jobs"

METADATA = [
    {"name": HEALTH, "description": "Who the caller is, and the health and totals of the service."},
    {"name": SEARCH, "description": "Semantic search over the collections the caller may read. A collection the caller "
                                    "cannot read looks the same as one that does not exist (`404`)."},
    {"name": COLLECTIONS, "description": "Collections and the sources inside them. Shared collections are open to every "
                                         "caller; restricted ones need a grant."},
    {"name": ACCESS, "description": "Access-control accounts. Admin only. A token is shown once, when it is created or "
                                    "rotated; only its hash is stored."},
    {"name": INGEST, "description": "Index one document, a repository pack, or a crawled website. Repository and crawl "
                                    "requests return a job (`202`)."},
    {"name": UPLOADS, "description": "Folder uploads in batches. Batches that share a `session` make one run; the final "
                                     "batch removes files that were not sent."},
    {"name": SOURCES, "description": "Saved folders: their rules and which collection each part goes to."},
    {"name": DOCS, "description": "Keeps a collection in step with a Forgejo docs repository: the push webhook "
                                  "(authenticated by signature) and a manual full resync."},
    {"name": JOBS, "description": "Status and the live log of background jobs. A job is visible to its owner and to admins."},
]
