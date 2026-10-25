"""Saved sync sources."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Principal
from .deps import Deps, check_slug
from .schemas import SourceCreate, SourceUpdate
from .tags import SOURCES


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[SOURCES])
    store_factory = d.store_factory
    current_principal = d.current_principal
    need_write = d.need_write

    # --- saved sync sources (F4/F5) ---

    def _source_visible(p: Principal, store, src: dict | None) -> dict:
        if src is None or store.collection_visibility(src["collection"]) is None or not p.can_read(
                src["collection"], store.collection_visibility(src["collection"])):
            raise HTTPException(404, "source not found")
        return src

    def _check_mapping(p: Principal, store, folder_collections: dict[str, str]) -> None:
        for folder, slug in folder_collections.items():
            if not folder or "/" in folder or "\\" in folder or folder == "..":
                raise HTTPException(422, "folder_collections keys are top-level folder names")
            check_slug(slug)
            need_write(p, store, slug)

    @router.get("/v1/sources")
    def list_sync_sources(p: Principal = Depends(current_principal)):
        """The saved folders the caller may read."""
        with store_factory() as store:
            vis = {c["slug"]: c["visibility"] for c in store.list_collections()}
            return {"sources": [s for s in store.list_sync_sources()
                                if s["collection"] in vis and p.can_read(s["collection"], vis[s["collection"]])]}

    @router.get("/v1/sources/{name}")
    def get_sync_source(name: str, p: Principal = Depends(current_principal)):
        """One saved folder, with its rules and last run."""
        with store_factory() as store:
            return _source_visible(p, store, store.get_sync_source(name))

    @router.post("/v1/sources", status_code=201)
    def create_sync_source(req: SourceCreate, p: Principal = Depends(current_principal)):
        """Save a folder's rules and target collection (and which top-level folders go to their own collections)."""
        check_slug(req.collection)
        req.settings.build()  # validates the rules
        with store_factory() as store:
            need_write(p, store, req.collection)
            _check_mapping(p, store, req.folder_collections)
            if not store.create_sync_source(req.name, req.kind, req.collection, req.settings.model_dump(),
                                            req.folder_collections, p.name):
                raise HTTPException(409, f"source {req.name!r} already exists")
            return store.get_sync_source(req.name)

    @router.patch("/v1/sources/{name}")
    def update_sync_source(name: str, req: SourceUpdate, p: Principal = Depends(current_principal)):
        """Change a saved folder's collection, rules or folder mapping."""
        with store_factory() as store:
            src = _source_visible(p, store, store.get_sync_source(name))
            need_write(p, store, src["collection"])
            if req.collection is not None:
                check_slug(req.collection)
                need_write(p, store, req.collection)
            if req.settings is not None:
                req.settings.build()
            if req.folder_collections is not None:
                _check_mapping(p, store, req.folder_collections)
            store.update_sync_source(name, req.collection, req.settings.model_dump() if req.settings else None,
                                     req.folder_collections)
            return store.get_sync_source(name)

    @router.delete("/v1/sources/{name}", status_code=204)
    def delete_sync_source(name: str, purge: bool = False, p: Principal = Depends(current_principal)):
        """Forget the source. With ``purge`` the files it indexed are removed too; otherwise they stay searchable."""
        with store_factory() as store:
            src = _source_visible(p, store, store.get_sync_source(name))
            need_write(p, store, src["collection"])
            if purge:
                for slug in {src["collection"], *src["folder_collections"].values()}:
                    need_write(p, store, slug)
                    prefix = f"files://{name}/"
                    for uri in [u for u in store.source_hashes(slug) if u.startswith(prefix)]:
                        store.delete_source(slug, uri)
            store.delete_sync_source(name)

    @router.post("/v1/sources/{name}/sync")
    def sync_source_now(name: str, p: Principal = Depends(current_principal)):
        """Refresh a saved folder from the server. Folders are read in the browser, so this answers `409`: drop the folder again."""
        with store_factory() as store:
            src = _source_visible(p, store, store.get_sync_source(name))
            need_write(p, store, src["collection"])
        # Only the server can re-read a source it can reach (mount, S3, Drive, Git); those readers arrive later.
        raise HTTPException(409, f"{src['kind']} sources are read in your browser: drop the folder again to refresh")

    return router
