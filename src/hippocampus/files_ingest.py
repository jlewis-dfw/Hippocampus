"""Batched file uploads that build up one sync run (folder drop, host command, mounts).

A run is an upload session: every batch is checked, diffed and indexed as it arrives, so a large folder stays under the
request size limit and the caller can show progress. Pruning happens once, when the final batch arrives, against the
rules the session started with: sources of this folder that were not sent, or were sent and are no longer eligible, go.
A file that is rejected (for a secret, say) is removed at once, whatever happens to the rest of the run.

Sources are ``files://<source>/<path>``. The caller is not trusted: every path, size and secret check is repeated here.
"""
from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from dataclasses import dataclass, field

from .diff import sync_collection
from .sync_core import FileFilter, exclude_path_parts, file_uri, scan_files

MAX_BATCH_FILES = 500
MAX_BATCH_CHARS = 20_000_000
MAX_SESSION_FILES = 20_000
SESSION_TTL_SECONDS = 3600
MAX_SESSIONS = 50


class SessionError(LookupError):
    """Unknown, expired or foreign session (the API answers 404 for all three)."""


class LimitError(ValueError):
    """A batch or run is over a size limit."""


def server_filter(flt: FileFilter) -> FileFilter:
    """The caller's rules plus the server-wide guard, which no client setting can remove."""
    return dataclasses.replace(flt, exclude_parts=tuple(dict.fromkeys(
        (*flt.exclude_parts, *exclude_path_parts()))))


@dataclass
class UploadSession:
    id: str
    owner: str
    collection: str
    source: str
    flt: FileFilter
    uris: set[str] = field(default_factory=set)
    totals: dict[str, int] = field(default_factory=lambda: dict.fromkeys(
        ("added", "changed", "unchanged", "removed", "skipped", "rejected", "files"), 0))
    created_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


class UploadSessions:
    def __init__(self, ttl: float | None = None, max_sessions: int | None = None):
        self._sessions: dict[str, UploadSession] = {}
        self._lock = threading.Lock()
        self._ttl, self._max = ttl or SESSION_TTL_SECONDS, max_sessions or MAX_SESSIONS

    def _expire(self) -> None:
        cutoff = time.time() - self._ttl
        for sid in [s for s, v in self._sessions.items() if v.created_at < cutoff]:
            del self._sessions[sid]

    def start(self, owner: str, collection: str, source: str, flt: FileFilter) -> UploadSession:
        with self._lock:
            self._expire()
            if len(self._sessions) >= self._max:
                raise LimitError("too many uploads in progress; try again shortly")
            s = UploadSession(uuid.uuid4().hex, owner, collection, source, server_filter(flt))
            self._sessions[s.id] = s
            return s

    def get(self, sid: str, owner: str, collection: str, source: str) -> UploadSession:
        with self._lock:
            self._expire()
            s = self._sessions.get(sid)
        if s is None or s.owner != owner or (s.collection, s.source) != (collection, source):
            raise SessionError(sid)
        return s

    def abort(self, sid: str, owner: str) -> UploadSession | None:
        with self._lock:
            s = self._sessions.get(sid)
            if s is None or s.owner != owner:
                return None
            return self._sessions.pop(sid)

    def end(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)


def preview(source: str, files, flt: FileFilter):
    """Decisions for ``files`` with nothing written. Content is checked too when the caller sent it."""
    return scan_files(source, files, server_filter(flt))


def process_batch(session: UploadSession, files, store, embedder) -> list[dict]:
    """Check, diff and index one batch of ``(path, text, size)``. Returns one result per file, in order."""
    with session.lock:
        if session.totals["files"] + len(files) > MAX_SESSION_FILES:
            raise LimitError(f"more than {MAX_SESSION_FILES} files in one run")
        scan = scan_files(session.source, files, session.flt)
        known = store.source_hashes(session.collection)
        result = sync_collection(session.collection, scan.documents, store, embedder, prune=False)
        action = {u: "added" for u in result.added} | {u: "changed" for u in result.changed} | {
            u: "unchanged" for u in result.unchanged}
        out = []
        for d in scan.decisions:
            uri = file_uri(session.source, d.path)
            row = d.view()
            if d.outcome == "indexed":
                row["action"] = action.get(uri, "skipped")
            elif d.outcome == "rejected" and not (d.reason or "").startswith("invalid-path") and uri in known:
                store.delete_source(session.collection, uri)  # a file that now carries a secret must not stay indexed
                session.uris.discard(uri)
                row["action"] = "removed"
                session.totals["removed"] += 1
            out.append(row)
        session.uris |= set(scan.documents)
        session.totals["files"] += len(files)
        session.totals["added"] += len(result.added)
        session.totals["changed"] += len(result.changed)
        session.totals["unchanged"] += len(result.unchanged)
        counts = scan.counts()
        session.totals["skipped"] += counts["skipped"]
        session.totals["rejected"] += counts["rejected"]
        return out


def finish(session: UploadSession, store) -> list[str]:
    """Remove this folder's sources that the run did not keep. Returns the removed paths."""
    with session.lock:
        prefix = file_uri(session.source, "")
        stale = sorted(u for u in store.source_hashes(session.collection)
                       if u.startswith(prefix) and u not in session.uris)
        for uri in stale:
            store.delete_source(session.collection, uri)
        session.totals["removed"] += len(stale)
        return [u[len(prefix):] for u in stale]
