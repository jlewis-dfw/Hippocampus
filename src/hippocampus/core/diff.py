"""SHA-256 incremental diffing (HIPPO-E02-F1-S02)."""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .chunker import Chunk, chunk_markdown
from .embedder import Embedder


@dataclass(frozen=True)
class Document:
    title: str | None
    text: str


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    embedding: list[float]


class Store(Protocol):
    def source_hashes(self, collection: str) -> dict[str, str]:
        """uri -> sha256 for every source in the collection."""

    def replace_source(
        self, collection: str, uri: str, title: str | None, sha256: str, chunks: list[EmbeddedChunk]
    ) -> None:
        """Atomically upsert the source and replace all of its chunks."""

    def delete_source(self, collection: str, uri: str) -> None:
        """Delete the source; its chunks must go with it."""


@dataclass
class SyncResult:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sync_collection(
    collection: str,
    documents: Mapping[str, Document],
    store: Store,
    embedder: Embedder,
    *,
    prune: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> SyncResult:
    """Bring ``collection`` in line with ``documents`` (uri -> content).

    Unchanged hashes are skipped; changed/new sources are chunked and embedded
    *before* the store is touched, so an embedding failure never purges old
    vectors. With ``prune`` set, sources absent from ``documents`` are deleted.
    ``progress(done, total)`` is called as each document starts (with how many are complete) and once at the end.
    """
    known = store.source_hashes(collection)
    result = SyncResult()
    for done, (uri, doc) in enumerate(documents.items(), 1):
        if progress:
            progress(done - 1, len(documents))  # documents completed so far
        digest = sha256_text(doc.text)
        if known.get(uri) == digest:
            result.unchanged.append(uri)
            continue
        chunks = chunk_markdown(doc.text)
        vectors = embedder.embed([c.content for c in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks")
        store.replace_source(
            collection, uri, doc.title, digest, [EmbeddedChunk(c, v) for c, v in zip(chunks, vectors, strict=True)]
        )
        (result.changed if uri in known else result.added).append(uri)
    if progress and documents:
        progress(len(documents), len(documents))
    if prune:
        for uri in known.keys() - documents.keys():
            store.delete_source(collection, uri)
            result.deleted.append(uri)
    return result
