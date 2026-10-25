"""the docs repo push-webhook sync (HIPPO-E03-F2-S02).

A Forgejo push webhook names the files a push added, changed or removed; only those are fetched (read-only
Forgejo API) and re-indexed, and unchanged content is skipped by the SHA-256 diff. A full resync lists the
tree for first fill and drift repair. Sources are ``repo://<repo>/<path>``, the same scheme as the Repomix
ingester.

Nothing matching ``HIPPO_EXCLUDE_PATH_PARTS`` is ever indexed: the check runs on every path,
on the webhook route and the resync route alike, before any file is fetched. Files that match a secret shape
are rejected whole, as in the Repomix ingester. Webhook payloads and file contents are untrusted data.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import quote

from ..core.diff import Document, sync_collection
from .repo_ingest import MAX_FILE_BYTES, scan_secrets
from .sync_core import exclude_path_parts

# Forgejo truncates the commit list of a large push; at or beyond this many commits we resync instead.
_log = logging.getLogger("hippocampus")
MAX_PAYLOAD_COMMITS = 20
SIGNATURE_HEADERS = ("x-forgejo-signature", "x-gitea-signature")


def is_indexable(path: str) -> bool:
    """Markdown only, never a path with an excluded fragment, never a path that escapes the repo."""
    lower = path.lower()
    if any(p in lower for p in exclude_path_parts()):
        return False
    if path.startswith("/") or ".." in path.split("/"):
        return False
    return lower.endswith(".md")


class DocsSource(Protocol):
    def list_files(self, ref: str) -> list[str]: ...

    def read_file(self, path: str, ref: str) -> str | None:
        """Text of the file at ``ref``, or None if it does not exist there."""


@dataclass(frozen=True)
class DocsSyncConfig:
    collection: str
    owner: str
    repo: str
    branch: str
    secret: str
    source: DocsSource

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    def uri(self, path: str) -> str:
        return f"repo://{self.repo}/{path}"


def verify_signature(secret: str, body: bytes, headers) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    for name in SIGNATURE_HEADERS:
        given = headers.get(name)
        if given and hmac.compare_digest(given.strip().lower().removeprefix("sha256="), expected):
            return True
    return False


@dataclass
class PushPlan:
    upserts: set[str] = field(default_factory=set)
    removes: set[str] = field(default_factory=set)
    full: bool = False
    ignored: str | None = None


def plan_push(cfg: DocsSyncConfig, payload: dict) -> PushPlan:
    repo = (payload.get("repository") or {}).get("full_name")
    if repo != cfg.full_name:
        return PushPlan(ignored=f"repository {repo!r} is not {cfg.full_name!r}")
    if payload.get("ref") != f"refs/heads/{cfg.branch}":
        return PushPlan(ignored=f"ref {payload.get('ref')!r} is not {cfg.branch!r}")
    commits = payload.get("commits") or []
    if not commits or len(commits) >= MAX_PAYLOAD_COMMITS:
        return PushPlan(full=True)
    plan = PushPlan()
    for commit in commits:  # oldest first: the last action on a path wins
        for path in (*(commit.get("added") or ()), *(commit.get("modified") or ())):
            plan.upserts.add(path)
            plan.removes.discard(path)
        for path in commit.get("removed") or ():
            plan.removes.add(path)
            plan.upserts.discard(path)
    return plan


def _result(**kw) -> dict:
    return {k: kw.get(k, 0) for k in ("added", "changed", "unchanged", "removed")} | {
        "rejected": kw.get("rejected", []), "skipped": kw.get("skipped", 0)}


def apply_changes(cfg: DocsSyncConfig, store, embedder, ref: str, upserts, removes,
                  log: Callable[[str], None] = lambda m: None) -> dict:
    """Re-index ``upserts`` and drop ``removes``. Paths outside ``is_indexable`` are skipped before any fetch."""
    documents: dict[str, Document] = {}
    gone = {cfg.uri(p) for p in removes if is_indexable(p)}
    rejected: list[dict] = []
    skipped = 0
    for path in sorted(upserts):
        if not is_indexable(path):
            skipped += 1
            continue
        text = cfg.source.read_file(path, ref)
        if text is None:
            gone.add(cfg.uri(path))
            continue
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            rejected.append({"path": path, "reasons": ["too-large"]})
            gone.add(cfg.uri(path))
            continue
        hits = scan_secrets(text)
        if hits:
            log(f"rejected {path}: {', '.join(hits)}")
            _log.warning("file rejected: %s (rule %s)", path, ", ".join(hits))
            rejected.append({"path": path, "reasons": hits})
            gone.add(cfg.uri(path))  # a file now carrying a secret must not stay indexed
            continue
        documents[cfg.uri(path)] = Document(None, text)
    result = sync_collection(cfg.collection, documents, store, embedder, prune=False)
    present = store.source_hashes(cfg.collection)
    removed = [u for u in sorted(gone) if u in present]
    for uri in removed:
        store.delete_source(cfg.collection, uri)
    log(f"done: {len(result.added)} added, {len(result.changed)} changed, {len(result.unchanged)} unchanged, "
        f"{len(removed)} removed, {len(rejected)} rejected, {skipped} skipped")
    return _result(added=len(result.added), changed=len(result.changed), unchanged=len(result.unchanged),
                   removed=len(removed), rejected=rejected, skipped=skipped)


def full_resync(cfg: DocsSyncConfig, store, embedder, log: Callable[[str], None] = lambda m: None) -> dict:
    """Index every eligible file on the branch and remove this repo's sources that are no longer eligible."""
    files = cfg.source.list_files(cfg.branch)
    eligible = {p for p in files if is_indexable(p)}
    log(f"{len(eligible)} of {len(files)} files eligible")
    prefix = cfg.uri("")
    stale = {u for u in store.source_hashes(cfg.collection)
             if u.startswith(prefix) and u[len(prefix):] not in eligible}
    result = apply_changes(cfg, store, embedder, cfg.branch, eligible, set(), log)
    # Paths that left the branch (or became ineligible) are removed by URI, so is_indexable is not applied.
    for uri in stale:
        store.delete_source(cfg.collection, uri)
    result["removed"] += len(stale)
    log(f"removed {len(stale)} stale sources")
    return result


class ForgejoSource:
    """Read-only Forgejo API client. The token needs read access to the one repository."""

    def __init__(self, base_url: str, owner: str, repo: str, token: str, timeout: float = 30.0):
        import httpx

        self._owner_repo = f"{quote(owner)}/{quote(repo)}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1", timeout=timeout, follow_redirects=False,
            headers={"Authorization": f"token {token}"})

    def list_files(self, ref: str) -> list[str]:
        paths: list[str] = []
        page = 1
        while True:
            r = self._client.get(f"/repos/{self._owner_repo}/git/trees/{quote(ref, safe='')}",
                                 params={"recursive": "true", "per_page": 1000, "page": page})
            r.raise_for_status()
            body = r.json()
            paths += [e["path"] for e in body.get("tree", []) if e.get("type") == "blob"]
            if not body.get("truncated"):
                return paths
            page += 1

    def read_file(self, path: str, ref: str) -> str | None:
        r = self._client.get(f"/repos/{self._owner_repo}/raw/{quote(path)}", params={"ref": ref})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content.decode("utf-8", errors="replace")
