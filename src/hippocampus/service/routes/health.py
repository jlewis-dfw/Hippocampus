"""Who am I, and is the service healthy."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ...core.embedder import DIMENSIONS, MODEL_NAME
from ..auth import Principal
from .deps import Deps
from .tags import HEALTH


def build(d: Deps) -> APIRouter:
    router = APIRouter(tags=[HEALTH])
    store_factory = d.store_factory
    jobs = d.jobs
    docs_sync_enabled = d.docs_sync is not None
    require_auth = d.require_auth
    crawl_require_ack = d.crawl_require_ack
    current_principal = d.current_principal
    readable = d.readable

    @router.get("/v1/whoami")
    def whoami(p: Principal = Depends(current_principal)):
        """The caller's identity, grants and whether writes to shared collections need a token."""
        return {**p.view(), "require_auth": require_auth}

    @router.get("/v1/status")
    def status(p: Principal = Depends(current_principal)):
        """Database round-trip time, running jobs, the embedding model, whether docs sync is configured, and totals for what the caller may read."""
        t0 = time.perf_counter()
        with store_factory() as store:
            totals = store.totals([c["slug"] for c in readable(p, store)])
        return {
            "db_latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "active_jobs": jobs.active(),
            "embedder": {"model": MODEL_NAME, "dimensions": DIMENSIONS},
            "docs_sync": docs_sync_enabled,
            "crawl_require_ack": crawl_require_ack,
            **totals,
        }

    return router
