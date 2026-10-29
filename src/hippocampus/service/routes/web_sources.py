"""Saved web sources: the crawl settings behind a collection, re-run and pruned from the dashboard."""
from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal
from .deps import Deps
from .schemas import CrawlRequest, PruneRequest, WebSourceUpdate
from .tags import WEB


def start_crawl_job(d: Deps, req: CrawlRequest, p: Principal, web_id: str):
    """Run the crawl as a job; afterwards remember each page's depth and the outcome on the web source."""
    crawler = d.crawler
    assert crawler is not None  # the routes answer 501 before they get here

    def work(job):
        try:
            result = crawler(job, req) or {}
        except Exception as exc:
            with d.store_factory() as store:
                store.record_web_run(web_id, "error", {"error": str(exc)[:500]})
            raise
        depths = result.pop("depths", {})
        with d.store_factory() as store:
            store.tag_web_pages(req.collection, web_id, depths)
            store.record_web_run(web_id, "ok", result)
        return result

    return d.jobs.submit("crawl", work, owner=p.name)


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[WEB])
    store_factory = d.store_factory
    current_principal = d.current_principal
    need_write = d.need_write

    def _visible(p: Principal, store, web: dict | None) -> dict:
        vis = store.collection_visibility(web["collection"]) if web else None
        if web is None or vis is None or not p.can_read(web["collection"], vis):
            raise HTTPException(404, "web source not found")  # a hidden one looks like a missing one
        return web

    @router.get("/v1/web-sources")
    def list_web_sources(p: Principal = Depends(current_principal)):
        """The saved crawls the caller may read, with how many pages each holds and how deep they go."""
        with store_factory() as store:
            vis = {c["slug"]: c["visibility"] for c in store.list_collections()}
            return {"web_sources": [w for w in store.list_web_sources()
                                    if w["collection"] in vis and p.can_read(w["collection"], vis[w["collection"]])]}

    @router.patch("/v1/web-sources/{web_id}")
    def update_web_source(web_id: UUID, req: WebSourceUpdate, p: Principal = Depends(current_principal)):
        """Change the depth, pattern or engine used by the next crawl. Pages already indexed stay (see prune)."""
        if req.match_pattern:
            try:
                re.compile(req.match_pattern)
            except re.error as exc:
                raise HTTPException(422, f"invalid match_pattern: {exc}") from exc
        with store_factory() as store:
            web = _visible(p, store, store.get_web_source(str(web_id)))
            need_write(p, store, web["collection"])
            store.update_web_source(str(web_id), req.depth, req.match_pattern or None, req.engine,
                                    clear_pattern=req.match_pattern == "")
            return store.get_web_source(str(web_id))

    @router.delete("/v1/web-sources/{web_id}", status_code=204)
    def delete_web_source(web_id: UUID, p: Principal = Depends(current_principal)):
        """Forget the saved settings. The pages it indexed stay in the collection."""
        with store_factory() as store:
            web = _visible(p, store, store.get_web_source(str(web_id)))
            need_write(p, store, web["collection"])
            store.delete_web_source(str(web_id))

    @router.post("/v1/web-sources/{web_id}/crawl", status_code=202)
    def crawl_web_source(web_id: UUID, p: Principal = Depends(current_principal)):
        """Crawl again with the saved settings. Unchanged pages are skipped. Runs as a job."""
        if d.crawler is None:
            raise HTTPException(501, "crawler not available (HIPPO-E03)")
        with store_factory() as store:
            web = _visible(p, store, store.get_web_source(str(web_id)))
            need_write(p, store, web["collection"])
        req = CrawlRequest(url=web["url"], collection=web["collection"], depth=web["depth"],
                           match_pattern=web["match_pattern"], engine=web["engine"])
        job = start_crawl_job(d, req, p, str(web_id))
        return {"job_id": job.id, "status": job.status}

    @router.post("/v1/web-sources/{web_id}/prune")
    def prune_web_source(web_id: UUID, req: PruneRequest, p: Principal = Depends(current_principal)):
        """Remove the pages this crawl found deeper than ``depth``. With ``dry_run`` (the default) only count them.

        Only pages crawled since saved web sources existed carry a depth; older pages are never removed.
        """
        with store_factory() as store:
            web = _visible(p, store, store.get_web_source(str(web_id)))
            need_write(p, store, web["collection"])
            return {"depth": req.depth, "dry_run": req.dry_run,
                    "pages": store.prune_web_source(str(web_id), req.depth, req.dry_run)}

    return router
