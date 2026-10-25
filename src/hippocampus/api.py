"""REST surface (HIPPO-E04-F1-S01). Network gate (E04-F1-S02) plus per-collection bearer-token access (E06-F0).

This file wires the application: middleware, the shared dependencies and the routers in ``routes/``.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .docs_sync import DocsSyncConfig, ForgejoSource
from .embedder import Embedder
from .jobs import JobRegistry
from .observability import log_requests, setup_logging
from .routes import ROUTERS
from .routes.deps import SLUG_RE, Crawler, Deps, StoreFactory  # noqa: F401  (re-exported)

UI_DIR = Path(__file__).parent / "ui"
# The dashboard can launch crawls and delete collections, so it loads nothing from outside its own origin.
UI_CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"

log = logging.getLogger("hippocampus")

# Interim network gate (HIPPO-E04-F1-S02). TEMPORARY: replaced by external JWT validation in HIPPO-E06.
# Defence in depth only: the primary gate is binding the published port to a private address (see compose.yaml, HIPPO_BIND).
DEFAULT_ALLOWED_CIDRS = "10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,::1/128"


def parse_networks(spec: str) -> list:
    return [ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip()]


def create_app(
    store_factory: StoreFactory,
    embedder: Embedder,
    jobs: JobRegistry | None = None,
    crawler: Crawler | None = None,
    allowed_networks: list | None = None,
    docs_sync: DocsSyncConfig | None = None,
    require_auth: bool = False,
) -> FastAPI:
    """``allowed_networks``: clients outside these CIDRs get 403 on /v1/*. None disables the check.

    ``require_auth``: writes to shared collections also need a write grant (anonymous stays read-only on shared).
    Restricted collections always need a grant, whatever this says.
    """
    app = FastAPI(title="Hippocampus", version="0.1.0")
    app.middleware("http")(log_requests)

    if allowed_networks is not None:

        @app.middleware("http")
        async def network_gate(request: Request, call_next):
            host = request.client.host if request.client else ""
            try:
                ok = any(ipaddress.ip_address(host) in n for n in allowed_networks)
            except ValueError:
                ok = False
            if not ok and request.url.path.startswith("/v1/"):
                return JSONResponse({"detail": "client network not allowed"}, status_code=403)
            return await call_next(request)

    deps = Deps(store_factory, embedder, jobs or JobRegistry(), crawler, docs_sync, require_auth)
    for module in ROUTERS:
        app.include_router(module.build(deps))

    @app.middleware("http")
    async def ui_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/ui"):
            response.headers["Content-Security-Policy"] = UI_CSP
            response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/ui/")

    if UI_DIR.is_dir():
        app.mount("/ui", StaticFiles(directory=UI_DIR, html=True), name="ui")

    return app

def app_from_env() -> FastAPI:
    """uvicorn --factory hippocampus.api:app_from_env  (HIPPO_DSN from the environment/secret)."""
    from .crawler import make_crawler
    from .embedder import FastEmbedder
    from .store import connect_store
    from .workers import check_single_worker

    setup_logging(os.environ)
    check_single_worker()
    log.info("starting: auth required=%s, networks=%s, crawl max pages=%s, docs sync=%s",
             os.environ.get("HIPPO_REQUIRE_AUTH", "false"), os.environ.get("HIPPO_ALLOWED_CIDRS", "default"),
             os.environ.get("HIPPO_CRAWL_MAX_PAGES", "200"), bool(os.environ.get("HIPPO_FORGEJO_URL")))
    factory = connect_store(os.environ["HIPPO_DSN"])
    embedder = FastEmbedder(cache_dir=os.environ.get("HIPPO_MODEL_CACHE"))
    return create_app(
        factory,
        embedder,
        crawler=make_crawler(
            factory, embedder,
            max_pages=int(os.environ.get("HIPPO_CRAWL_MAX_PAGES", "200")),
            delay=float(os.environ.get("HIPPO_CRAWL_DELAY", "0.5")),
        ),
        allowed_networks=parse_networks(os.environ.get("HIPPO_ALLOWED_CIDRS", DEFAULT_ALLOWED_CIDRS)),
        docs_sync=_docs_sync_from_env(),
        require_auth=os.environ.get("HIPPO_REQUIRE_AUTH", "").lower() in ("1", "true", "yes"),
    )


def _docs_sync_from_env() -> DocsSyncConfig | None:
    """Enabled only when the Forgejo URL, a read-only token and the webhook secret are all provided."""
    url, token, secret = (os.environ.get(k) for k in ("HIPPO_FORGEJO_URL", "HIPPO_FORGEJO_TOKEN", "HIPPO_WEBHOOK_SECRET"))
    if not (url and token and secret):
        return None
    owner = os.environ.get("HIPPO_DOCS_OWNER", "your-org")
    repo = os.environ.get("HIPPO_DOCS_REPO", "your-docs-repo")
    return DocsSyncConfig(
        collection=os.environ.get("HIPPO_DOCS_COLLECTION", "docs"), owner=owner, repo=repo,
        branch=os.environ.get("HIPPO_DOCS_BRANCH", "main"), secret=secret,
        source=ForgejoSource(url, owner, repo, token))
