"""Who am I, and is the service healthy."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ..auth import Principal
from ..embedder import DIMENSIONS, MODEL_NAME
from .deps import Deps


def build(d: Deps) -> APIRouter:
    router = APIRouter()
    store_factory = d.store_factory
    jobs = d.jobs
    require_auth = d.require_auth
    current_principal = d.current_principal
    readable = d.readable

    @router.get("/v1/whoami")
    def whoami(p: Principal = Depends(current_principal)):
        return {**p.view(), "require_auth": require_auth}

    @router.get("/v1/status")
    def status(p: Principal = Depends(current_principal)):
        t0 = time.perf_counter()
        with store_factory() as store:
            totals = store.totals([c["slug"] for c in readable(p, store)])
        return {
            "db_latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "active_jobs": jobs.active(),
            "embedder": {"model": MODEL_NAME, "dimensions": DIMENSIONS},
            **totals,
        }

    return router
