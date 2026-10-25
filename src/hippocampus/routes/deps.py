"""What every router shares: the collaborators handed to ``create_app`` and the access checks built on them."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from fastapi import HTTPException, Request

from ..auth import ANONYMOUS, AuthError, Principal, authenticate
from ..docs_sync import DocsSyncConfig
from ..embedder import Embedder
from ..jobs import Job, JobRegistry
from ..observability import client_address
from .schemas import CrawlRequest

log = logging.getLogger("hippocampus")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

StoreFactory = Callable[[], AbstractContextManager]
Crawler = Callable[[Job, CrawlRequest], dict | None]


def check_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise HTTPException(422, f"invalid collection slug {slug!r}")


@dataclass
class Deps:
    store_factory: StoreFactory
    embedder: Embedder
    jobs: JobRegistry
    crawler: Crawler | None = None
    docs_sync: DocsSyncConfig | None = None
    require_auth: bool = False

    # --- access control (HIPPO-E06-F0) ---

    def current_principal(self, request: Request) -> Principal:
        header, alt = request.headers.get("authorization"), request.headers.get("x-hippo-token")
        if not (header or alt):
            return ANONYMOUS
        try:
            with self.store_factory() as store:
                return authenticate(header, store, alt)
        except AuthError as exc:
            log.warning("authentication failed: %s", exc, extra={"client": client_address(request), "path": request.url.path})
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    def need_read(self, p: Principal, store, slug: str) -> str:
        """Visibility of a collection the principal may read. A hidden collection looks exactly like a missing one."""
        vis = store.collection_visibility(slug)
        if vis is None or not p.can_read(slug, vis):
            raise HTTPException(404, f"collection {slug!r} not found")
        return vis

    def need_write(self, p: Principal, store, slug: str) -> str:
        vis = self.need_read(p, store, slug)
        if not p.can_write(slug, vis, require_auth=self.require_auth):
            raise HTTPException(403, f"writing to collection {slug!r} needs a token with a write grant")
        return vis

    def need_admin(self, p: Principal) -> None:
        if not p.admin:
            raise HTTPException(403, "admin token required")

    def readable(self, p: Principal, store) -> list[dict]:
        return [c for c in store.list_collections() if p.can_read(c["slug"], c["visibility"])]
