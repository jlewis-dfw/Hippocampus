"""Folder uploads: batches that make one run, and the saved source rules that apply to it."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import Principal
from ..files_ingest import MAX_BATCH_CHARS, LimitError, SessionError, UploadSessions, finish, preview, process_batch
from ..sync_core import FileFilter
from .deps import Deps, check_slug
from .schemas import FilterSpec, IngestFilesRequest
from .tags import UPLOADS


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[UPLOADS])
    store_factory = d.store_factory
    embedder = d.embedder
    jobs = d.jobs
    current_principal = d.current_principal
    need_write = d.need_write

    uploads = UploadSessions()

    def run_filter(src: dict | None, collection: str, requested: FilterSpec) -> FileFilter:
        """A saved source's rules win over the request's. Folders mapped to their own collection go only there."""
        if src is None:
            return requested.build()
        spec = FilterSpec(**src["settings"])
        mapping = src["folder_collections"]
        if collection == src["collection"]:  # everything not sent to a collection of its own
            return FilterSpec(**{**src["settings"], "exclude": [*spec.exclude, *(f"/{f}/" for f in mapping)]}).build()
        mapped = [f for f, slug in mapping.items() if slug == collection]
        roots = [f for f in mapped if not spec.folders or f in spec.folders]
        return spec.build(roots or ["\x00"])  # empty roots would mean "everything"; this matches nothing

    def saved_source(store, name: str, collection: str, release: bool) -> dict | None:
        """The saved source of this name, if it uses ``collection``. A ``release`` (an empty final batch) may also name a
        collection the source no longer uses, to remove the files it left there."""
        src = store.get_sync_source(name)
        if src is not None and collection not in (src["collection"], *src["folder_collections"].values()):
            if not release:
                raise HTTPException(409, f"{name!r} is a saved source for another collection")
            return None
        return src

    def record(store, name: str, status: str, summary: dict) -> None:
        store.record_sync_run(name, status, summary)

    @router.post("/v1/ingest/files")
    def ingest_files(req: IngestFilesRequest, p: Principal = Depends(current_principal)):
        """One batch of a folder upload. Many batches with one ``session`` make one run; ``final`` closes it and prunes."""
        check_slug(req.collection)
        if sum(len(f.content or "") for f in req.files) > MAX_BATCH_CHARS:
            raise HTTPException(413, "batch is too large; send fewer files per request")
        with store_factory() as store:
            need_write(p, store, req.collection)
            src = saved_source(store, req.source, req.collection, req.final and not req.files and not req.session)
            flt = run_filter(src, req.collection, req.filter)
            if req.dry_run:
                scan = preview(req.source, [(f.path, f.content, f.size) for f in req.files], flt)
                return {"dry_run": True, "counts": scan.counts(), "files": [d.view() for d in scan.decisions]}
            if any(f.content is None for f in req.files):
                raise HTTPException(422, "every file needs its content")
            try:
                if req.session:
                    session = uploads.get(req.session, p.name, req.collection, req.source)
                else:
                    session = uploads.start(p.name, req.collection, req.source, flt)
            except SessionError as exc:
                raise HTTPException(404, "upload session not found or expired") from exc
            except LimitError as exc:
                raise HTTPException(429, str(exc)) from exc
            try:
                results = process_batch(session, [(f.path, f.content, f.size) for f in req.files], store, embedder)
            except LimitError as exc:
                raise HTTPException(413, str(exc)) from exc
            except Exception as exc:  # the index is as it was: pruning only happens on the final batch
                uploads.end(session.id)
                if src is not None:
                    record(store, src["name"], "error", {"collection": req.collection, "error": type(exc).__name__})
                raise
            removed: list[str] = []
            job_id = None
            if req.final:
                removed = finish(session, store)
                uploads.end(session.id)
                summary = dict(session.totals)
                job_id = jobs.submit("sync-files", lambda j: {"source": req.source, "collection": req.collection,
                                                             **summary}, background=False, owner=p.name).id
                if src is not None:
                    record(store, src["name"], "ok", {"collection": req.collection, **summary})
        return {"session": None if req.final else session.id, "source": req.source, "files": results,
                "removed": removed, "totals": dict(session.totals), "job_id": job_id}

    @router.delete("/v1/ingest/files/{session_id}", status_code=204)
    def abort_ingest_files(session_id: str, reason: str = Query("cancelled", max_length=100),
                           p: Principal = Depends(current_principal)):
        """Cancel a run. Nothing is pruned, so the index keeps what it had; a saved source shows why the run stopped."""
        session = uploads.abort(session_id, p.name)
        if session is None:
            raise HTTPException(404, "upload session not found or expired")
        with store_factory() as store:
            if store.get_sync_source(session.source) is not None:
                record(store, session.source, "error", {"collection": session.collection, "error": reason})

    return router
