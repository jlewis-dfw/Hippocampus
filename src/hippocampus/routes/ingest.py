"""Single-document, repository and crawl ingestion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import PRIVATE_URI_PREFIXES, Principal
from ..diff import Document, sync_collection
from ..repo_ingest import ingest_pack, parse_pack
from ..sync_core import exclude_path_parts
from .deps import Deps, check_slug
from .schemas import CrawlRequest, IngestFileRequest, IngestRepoRequest
from .tags import INGEST


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[INGEST])
    store_factory = d.store_factory
    embedder = d.embedder
    jobs = d.jobs
    crawler = d.crawler
    current_principal = d.current_principal
    need_write = d.need_write

    @router.post("/v1/ingest/file")
    def ingest_file(req: IngestFileRequest, p: Principal = Depends(current_principal)):
        """Index one document (text) under a URI. An unchanged document is skipped."""
        check_slug(req.collection)
        if any(part in req.uri.lower() for part in exclude_path_parts()):
            raise HTTPException(403, "this source is excluded from ingestion")
        with store_factory() as store:
            vis = need_write(p, store, req.collection)
            if req.uri.lower().startswith(PRIVATE_URI_PREFIXES) and vis != "restricted":
                raise HTTPException(403, "private story sources may only be indexed into a restricted collection")
            result = sync_collection(
                req.collection, {req.uri: Document(req.title, req.text)}, store, embedder, prune=False
            )
        return {"added": result.added, "changed": result.changed, "unchanged": result.unchanged}

    @router.post("/v1/ingest/repo", status_code=202)
    def ingest_repo(req: IngestRepoRequest, p: Principal = Depends(current_principal)):
        """Index a repository pack (`repomix --style xml|markdown`). Runs as a job; files with secrets are rejected."""
        check_slug(req.collection)
        if any(part in req.repo.lower() for part in exclude_path_parts()):
            raise HTTPException(403, "this source is excluded from ingestion")
        try:
            parse_pack(req.pack)  # reject an unreadable pack up front, not inside the job
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store_factory() as store:
            need_write(p, store, req.collection)

        def work(job):
            with store_factory() as store:
                return ingest_pack(req.collection, req.repo, req.pack, store, embedder, job.log)

        job = jobs.submit("ingest-repo", work, owner=p.name)
        return {"job_id": job.id, "status": job.status}


    @router.post("/v1/crawl", status_code=202)
    def crawl(req: CrawlRequest, p: Principal = Depends(current_principal)):
        """Crawl a website into a collection (static or browser engine, to a depth of 5). Runs as a job."""
        check_slug(req.collection)
        if crawler is None:
            raise HTTPException(501, "crawler not available (HIPPO-E03)")
        if any(p in req.url.lower() for p in exclude_path_parts()):
            raise HTTPException(403, "this source is excluded from ingestion")
        with store_factory() as store:
            need_write(p, store, req.collection)
        job = jobs.submit("crawl", lambda j: crawler(j, req), owner=p.name)
        return {"job_id": job.id, "status": job.status}

    return router
