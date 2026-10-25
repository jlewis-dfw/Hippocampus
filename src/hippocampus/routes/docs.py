"""Docs repository sync: the Forgejo webhook and the manual full resync."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import Principal
from ..docs_sync import DocsSyncConfig, apply_changes, full_resync, plan_push, verify_signature
from .deps import Deps


def build(d: Deps) -> APIRouter:
    router = APIRouter()
    store_factory = d.store_factory
    embedder = d.embedder
    jobs = d.jobs
    docs_sync = d.docs_sync
    current_principal = d.current_principal
    need_write = d.need_write

    def _docs_collection_exists(cfg: DocsSyncConfig) -> bool:
        with store_factory() as store:
            return store.collection_exists(cfg.collection)

    @router.post("/v1/webhooks/forgejo", status_code=202)
    async def forgejo_webhook(request: Request):
        """Forgejo push webhook for the docs repo (HIPPO-E03-F2-S02). Authenticated by HMAC, not by the network gate."""
        if docs_sync is None:
            raise HTTPException(501, "docs sync not configured")
        body = await request.body()
        if not verify_signature(docs_sync.secret, body, request.headers):
            raise HTTPException(401, "bad signature")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise HTTPException(422, "payload is not JSON") from exc
        plan = plan_push(docs_sync, payload if isinstance(payload, dict) else {})
        if plan.ignored:
            return {"ignored": plan.ignored}
        if not _docs_collection_exists(docs_sync):
            raise HTTPException(404, f"collection {docs_sync.collection!r} not found")
        ref = payload.get("after") or docs_sync.branch

        def work(job):
            with store_factory() as store:
                if plan.full:
                    return full_resync(docs_sync, store, embedder, job.log)
                return apply_changes(docs_sync, store, embedder, ref, plan.upserts, plan.removes, job.log)

        job = jobs.submit("docs-sync", work, owner="system")
        return {"job_id": job.id, "status": job.status, "mode": "full" if plan.full else "incremental"}

    @router.post("/v1/sync/docs", status_code=202)
    def sync_docs(p: Principal = Depends(current_principal)):
        if docs_sync is None:
            raise HTTPException(501, "docs sync not configured")
        with store_factory() as store:
            need_write(p, store, docs_sync.collection)

        def work(job):
            with store_factory() as store:
                return full_resync(docs_sync, store, embedder, job.log)

        job = jobs.submit("docs-resync", work, owner=p.name)
        return {"job_id": job.id, "status": job.status}

    return router
