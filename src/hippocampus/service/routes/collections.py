"""Collections: list, create, update, delete, and the sources inside one."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Principal, is_private_slug
from .deps import Deps, check_slug
from .schemas import CollectionCreate, CollectionUpdate, SourcesRemove
from .tags import COLLECTIONS


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[COLLECTIONS])
    store_factory = d.store_factory
    require_auth = d.require_auth
    current_principal = d.current_principal
    need_read = d.need_read
    need_write = d.need_write
    need_admin = d.need_admin
    readable = d.readable

    @router.get("/v1/collections")
    def list_collections(p: Principal = Depends(current_principal)):
        """The collections the caller may read."""
        with store_factory() as store:
            return {"collections": readable(p, store)}

    @router.get("/v1/collections/{slug}/sources")
    def list_sources(slug: str, p: Principal = Depends(current_principal), limit: int = Query(200, ge=1, le=1000)):
        """The indexed sources (documents) in one collection, with their chunk counts."""
        check_slug(slug)
        with store_factory() as store:
            need_read(p, store, slug)
            rows = store.list_sources(slug, limit)
        if rows is None:
            raise HTTPException(404, f"collection {slug!r} not found")
        return {"collection": slug, "sources": rows}

    @router.delete("/v1/collections/{slug}/sources")
    def remove_sources(slug: str, req: SourcesRemove, p: Principal = Depends(current_principal)):
        """Remove documents (and their chunks) from a collection. A crawled or synced one comes back on the next run unless excluded."""
        check_slug(slug)
        with store_factory() as store:
            need_write(p, store, slug)
            return {"collection": slug, "removed": store.delete_sources(slug, req.uris)}

    @router.post("/v1/collections", status_code=201)
    def create_collection(req: CollectionCreate, p: Principal = Depends(current_principal)):
        """Create a collection. A restricted collection needs an admin token."""
        check_slug(req.slug)
        if is_private_slug(req.slug) and req.visibility != "restricted":
            raise HTTPException(422, "collections named private* hold private data and must be restricted")
        if req.visibility == "restricted":
            need_admin(p)
        elif not p.can_write(req.slug, "shared", require_auth=require_auth):
            raise HTTPException(403, "creating a collection needs a token with a write grant")
        with store_factory() as store:
            if not store.create_collection(req.slug, req.name, req.description, req.visibility):
                existing = store.collection_visibility(req.slug)
                if existing is not None and not p.can_read(req.slug, existing):
                    raise HTTPException(403, "this collection name cannot be used")
                raise HTTPException(409, f"collection {req.slug!r} already exists")
        return {"slug": req.slug, "name": req.name, "description": req.description, "visibility": req.visibility}

    @router.patch("/v1/collections/{slug}")
    def update_collection(slug: str, req: CollectionUpdate, p: Principal = Depends(current_principal)):
        """Rename a collection or change its description or visibility. Admin only."""
        check_slug(slug)
        with store_factory() as store:
            need_read(p, store, slug)
            need_admin(p)
            if req.visibility == "shared" and is_private_slug(slug):
                raise HTTPException(422, "collections named private* hold private data and must stay restricted")
            store.update_collection(slug, req.name, req.description, req.visibility)
            return next(c for c in store.list_collections() if c["slug"] == slug)

    @router.delete("/v1/collections/{slug}", status_code=204)
    def delete_collection(slug: str, p: Principal = Depends(current_principal)):
        """Delete a collection and everything indexed in it. Restricted collections need an admin token."""
        check_slug(slug)
        with store_factory() as store:
            vis = need_read(p, store, slug)
            if vis == "restricted":
                need_admin(p)
            else:
                need_write(p, store, slug)
            if not store.delete_collection(slug):
                raise HTTPException(404, f"collection {slug!r} not found")

    return router
