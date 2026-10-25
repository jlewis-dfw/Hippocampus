"""Semantic search over the collections the caller may read."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import Principal
from .deps import Deps, check_slug
from .schemas import SearchRequest
from .tags import SEARCH


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[SEARCH])
    store_factory = d.store_factory
    embedder = d.embedder
    current_principal = d.current_principal
    need_read = d.need_read
    readable = d.readable

    @router.post("/v1/search")
    def search(req: SearchRequest, p: Principal = Depends(current_principal)):
        """Search the named collections, or every collection the caller may read when none are named. Scores are cosine similarity."""
        for slug in req.collections:
            check_slug(slug)
        vector = embedder.embed([req.query])[0]
        with store_factory() as store:
            if req.collections:
                for slug in req.collections:
                    need_read(p, store, slug)
                scope = req.collections
            else:  # "everything I may read", never "everything"
                scope = [c["slug"] for c in readable(p, store)]
            hits = store.search(vector, scope, req.top_k, req.min_score)
        return {"query": req.query, "results": hits}

    return router
