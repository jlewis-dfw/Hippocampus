"""REST surface (HIPPO-E04-F1-S01). Network gate (E04-F1-S02) plus per-collection bearer-token access (E06-F0)."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import (
    ANONYMOUS,
    CLIENT_NAME_RE,
    PRIVATE_URI_PREFIXES,
    AuthError,
    Principal,
    authenticate,
    hash_token,
    is_private_slug,
    new_token,
    validate_grants,
)
from .diff import Document, sync_collection
from .docs_sync import DocsSyncConfig, ForgejoSource, apply_changes, full_resync, plan_push, verify_signature
from .embedder import DIMENSIONS, MODEL_NAME, Embedder
from .jobs import Job, JobRegistry
from .repo_ingest import ingest_pack, parse_pack

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Infra docs must never enter a shared embedding store (specs.md section 1).
FORBIDDEN_URI_PARTS = ("private-infra",)

UI_DIR = Path(__file__).parent / "ui"
# The dashboard can launch crawls and delete collections, so it loads nothing from outside its own origin.
UI_CSP = "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"

StoreFactory = Callable[[], AbstractContextManager]
Crawler = Callable[[Job, "CrawlRequest"], dict | None]


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    collections: list[str] = []
    top_k: int = Field(5, ge=1, le=50)
    min_score: float = Field(0.0, ge=-1.0, le=1.0)


class CollectionCreate(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["shared", "restricted"] = "shared"


class CollectionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    visibility: Literal["shared", "restricted"] | None = None


class ClientCreate(BaseModel):
    name: str
    admin: bool = False
    read: list[str] = []
    write: list[str] = []
    note: str | None = Field(None, max_length=500)


class ClientUpdate(BaseModel):
    admin: bool | None = None
    read: list[str] | None = None
    write: list[str] | None = None
    note: str | None = Field(None, max_length=500)


class IngestFileRequest(BaseModel):
    collection: str
    uri: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(None, max_length=512)
    text: str


class IngestRepoRequest(BaseModel):
    collection: str
    repo: str = Field(pattern=r"^[A-Za-z0-9._-]{1,100}$")
    pack: str = Field(min_length=1, max_length=20_000_000)  # `repomix --style xml|markdown` output


class CrawlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    collection: str
    depth: int = Field(1, ge=1, le=5)
    match_pattern: str | None = None
    engine: Literal["static", "browser"] = "static"


# Interim network gate (HIPPO-E04-F1-S02). TEMPORARY: replaced by external JWT validation in HIPPO-E06.
# Defence in depth only: the primary gate is binding the published port to a private address (see compose.yaml, HIPPO_BIND).
DEFAULT_ALLOWED_CIDRS = "10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,::1/128"


def parse_networks(spec: str) -> list:
    return [ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip()]


def _check_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise HTTPException(422, f"invalid collection slug {slug!r}")


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

    jobs = jobs or JobRegistry()

    # --- access control (HIPPO-E06-F0) ---

    def current_principal(request: Request) -> Principal:
        header, alt = request.headers.get("authorization"), request.headers.get("x-hippo-token")
        if not (header or alt):
            return ANONYMOUS
        try:
            with store_factory() as store:
                return authenticate(header, store, alt)
        except AuthError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc

    def need_read(p: Principal, store, slug: str) -> str:
        """Visibility of a collection the principal may read. A hidden collection looks exactly like a missing one."""
        vis = store.collection_visibility(slug)
        if vis is None or not p.can_read(slug, vis):
            raise HTTPException(404, f"collection {slug!r} not found")
        return vis

    def need_write(p: Principal, store, slug: str) -> str:
        vis = need_read(p, store, slug)
        if not p.can_write(slug, vis, require_auth=require_auth):
            raise HTTPException(403, f"writing to collection {slug!r} needs a token with a write grant")
        return vis

    def need_admin(p: Principal) -> None:
        if not p.admin:
            raise HTTPException(403, "admin token required")

    def readable(p: Principal, store) -> list[dict]:
        return [c for c in store.list_collections() if p.can_read(c["slug"], c["visibility"])]

    @app.get("/v1/whoami")
    def whoami(p: Principal = Depends(current_principal)):
        return {**p.view(), "require_auth": require_auth}

    @app.post("/v1/search")
    def search(req: SearchRequest, p: Principal = Depends(current_principal)):
        for slug in req.collections:
            _check_slug(slug)
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

    @app.get("/v1/collections")
    def list_collections(p: Principal = Depends(current_principal)):
        with store_factory() as store:
            return {"collections": readable(p, store)}

    @app.get("/v1/collections/{slug}/sources")
    def list_sources(slug: str, p: Principal = Depends(current_principal), limit: int = Query(200, ge=1, le=1000)):
        _check_slug(slug)
        with store_factory() as store:
            need_read(p, store, slug)
            rows = store.list_sources(slug, limit)
        if rows is None:
            raise HTTPException(404, f"collection {slug!r} not found")
        return {"collection": slug, "sources": rows}

    @app.post("/v1/collections", status_code=201)
    def create_collection(req: CollectionCreate, p: Principal = Depends(current_principal)):
        _check_slug(req.slug)
        if is_private_slug(req.slug) and req.visibility != "restricted":
            raise HTTPException(422, "collections named jstories* hold private data and must be restricted")
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

    @app.patch("/v1/collections/{slug}")
    def update_collection(slug: str, req: CollectionUpdate, p: Principal = Depends(current_principal)):
        _check_slug(slug)
        with store_factory() as store:
            need_read(p, store, slug)
            need_admin(p)
            if req.visibility == "shared" and is_private_slug(slug):
                raise HTTPException(422, "collections named jstories* hold private data and must stay restricted")
            store.update_collection(slug, req.name, req.description, req.visibility)
            return next(c for c in store.list_collections() if c["slug"] == slug)

    @app.delete("/v1/collections/{slug}", status_code=204)
    def delete_collection(slug: str, p: Principal = Depends(current_principal)):
        _check_slug(slug)
        with store_factory() as store:
            vis = need_read(p, store, slug)
            if vis == "restricted":
                need_admin(p)
            else:
                need_write(p, store, slug)
            if not store.delete_collection(slug):
                raise HTTPException(404, f"collection {slug!r} not found")

    @app.get("/v1/clients")
    def list_clients(p: Principal = Depends(current_principal)):
        need_admin(p)
        with store_factory() as store:
            return {"clients": store.list_clients()}

    @app.post("/v1/clients", status_code=201)
    def create_client(req: ClientCreate, p: Principal = Depends(current_principal)):
        need_admin(p)
        if not CLIENT_NAME_RE.match(req.name):
            raise HTTPException(422, f"invalid client name {req.name!r}")
        try:
            read, write = validate_grants(req.read), validate_grants(req.write)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        token = new_token()
        with store_factory() as store:
            if not store.create_client(req.name, hash_token(token), req.admin, read, write, req.note):
                raise HTTPException(409, f"client {req.name!r} already exists")
        # The only time the token is ever shown: only its hash is stored.
        return {"name": req.name, "token": token, "admin": req.admin, "read": read, "write": write}

    def _last_admin(store, name: str) -> bool:
        target = next((c for c in store.list_clients() if c["name"] == name and not c["revoked"]), None)
        return bool(target and target["admin"] and store.count_admins() <= 1)

    @app.patch("/v1/clients/{name}")
    def update_client(name: str, req: ClientUpdate, p: Principal = Depends(current_principal)):
        need_admin(p)
        try:
            read = validate_grants(req.read) if req.read is not None else None
            write = validate_grants(req.write) if req.write is not None else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store_factory() as store:
            if req.admin is False and _last_admin(store, name):
                raise HTTPException(409, "cannot remove the last admin")
            if not store.update_client(name, req.admin, read, write, req.note):
                raise HTTPException(404, f"client {name!r} not found")
            return next(c for c in store.list_clients() if c["name"] == name)

    @app.post("/v1/clients/{name}/rotate")
    def rotate_client(name: str, p: Principal = Depends(current_principal)):
        """New token for the same client (name and grants unchanged). The old token stops working immediately."""
        need_admin(p)
        token = new_token()
        with store_factory() as store:
            if not store.rotate_client(name, hash_token(token)):
                raise HTTPException(404, f"client {name!r} not found")
        return {"name": name, "token": token}  # shown once; only the hash is stored

    @app.delete("/v1/clients/{name}", status_code=204)
    def revoke_client(name: str, p: Principal = Depends(current_principal)):
        need_admin(p)
        with store_factory() as store:
            if _last_admin(store, name):
                raise HTTPException(409, "cannot revoke the last admin")
            if not store.revoke_client(name):
                raise HTTPException(404, f"client {name!r} not found")

    @app.post("/v1/ingest/file")
    def ingest_file(req: IngestFileRequest, p: Principal = Depends(current_principal)):
        _check_slug(req.collection)
        if any(part in req.uri.lower() for part in FORBIDDEN_URI_PARTS):
            raise HTTPException(403, "this source is excluded from ingestion")
        with store_factory() as store:
            vis = need_write(p, store, req.collection)
            if req.uri.lower().startswith(PRIVATE_URI_PREFIXES) and vis != "restricted":
                raise HTTPException(403, "private story sources may only be indexed into a restricted collection")
            result = sync_collection(
                req.collection, {req.uri: Document(req.title, req.text)}, store, embedder, prune=False
            )
        return {"added": result.added, "changed": result.changed, "unchanged": result.unchanged}

    @app.post("/v1/ingest/repo", status_code=202)
    def ingest_repo(req: IngestRepoRequest, p: Principal = Depends(current_principal)):
        _check_slug(req.collection)
        if any(part in req.repo.lower() for part in FORBIDDEN_URI_PARTS):
            raise HTTPException(403, "this source is excluded from ingestion")
        try:
            parse_pack(req.pack)  # reject an unreadable pack up front, not inside the job
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with store_factory() as store:
            need_write(p, store, req.collection)

        def work(job):
            with store_factory() as store:
                return ingest_pack(req.collection, req.repo, req.pack, store, embedder, job.log)

        job = jobs.submit("ingest-repo", work, owner=p.name)
        return {"job_id": job.id, "status": job.status}

    def _docs_collection_exists(cfg: DocsSyncConfig) -> bool:
        with store_factory() as store:
            return store.collection_exists(cfg.collection)

    @app.post("/v1/webhooks/forgejo", status_code=202)
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

    @app.post("/v1/sync/docs", status_code=202)
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

    @app.post("/v1/crawl", status_code=202)
    def crawl(req: CrawlRequest, p: Principal = Depends(current_principal)):
        _check_slug(req.collection)
        if crawler is None:
            raise HTTPException(501, "crawler not available (HIPPO-E03)")
        if any(p in req.url.lower() for p in FORBIDDEN_URI_PARTS):
            raise HTTPException(403, "this source is excluded from ingestion")
        with store_factory() as store:
            need_write(p, store, req.collection)
        job = jobs.submit("crawl", lambda j: crawler(j, req), owner=p.name)
        return {"job_id": job.id, "status": job.status}

    def _job(job_id: str, p: Principal) -> Job:
        job = jobs.get(job_id)
        # A job's log and result name the paths it touched: only its owner (or an admin) sees them.
        if job is None or not (p.admin or job.owner in (p.name, "system")):
            raise HTTPException(404, "job not found")
        return job

    @app.get("/v1/jobs/{job_id}")
    def job_status(job_id: str, p: Principal = Depends(current_principal)):
        return _job(job_id, p).view()

    @app.get("/v1/jobs/{job_id}/events")
    def job_events(job_id: str, p: Principal = Depends(current_principal)):
        job = _job(job_id, p)

        def stream():
            sent = 0
            while True:
                finished = job.finished  # read before the log so a final event is never missed
                while sent < len(job.events):
                    yield f"data: {job.events[sent]}\n\n"
                    sent += 1
                if finished:
                    yield f"event: end\ndata: {job.status}\n\n"
                    return
                time.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/v1/status")
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
