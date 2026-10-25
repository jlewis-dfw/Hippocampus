"""Postgres/pgvector store for the ``hippocampus`` schema (needs the ``db`` extra)."""
from __future__ import annotations

from .diff import EmbeddedChunk


def _vec(v: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in v) + "]"


class PgStore:
    def __init__(self, conn):
        """``conn``: an open psycopg connection. The collection must already exist."""
        self._conn = conn

    def _collection_id(self, cur, slug: str):
        cur.execute("SELECT id FROM hippocampus.collections WHERE slug = %s", (slug,))
        row = cur.fetchone()
        if row is None:
            raise LookupError(f"collection {slug!r} does not exist")
        return row[0]

    def source_hashes(self, collection: str) -> dict[str, str]:
        with self._conn.cursor() as cur:
            cid = self._collection_id(cur, collection)
            cur.execute("SELECT uri, sha256_hash FROM hippocampus.sources WHERE collection_id = %s", (cid,))
            return {uri: h for uri, h in cur.fetchall()}

    def replace_source(self, collection, uri, title, sha256, chunks: list[EmbeddedChunk]) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cid = self._collection_id(cur, collection)
            cur.execute(
                """INSERT INTO hippocampus.sources (collection_id, uri, title, sha256_hash, chunk_count, status, last_indexed_at)
                   VALUES (%s, %s, %s, %s, %s, 'indexed', NOW())
                   ON CONFLICT (collection_id, uri) DO UPDATE
                   SET title = EXCLUDED.title, sha256_hash = EXCLUDED.sha256_hash,
                       chunk_count = EXCLUDED.chunk_count, status = 'indexed',
                       error_message = NULL, last_indexed_at = NOW()
                   RETURNING id""",
                (cid, uri, title, sha256, len(chunks)),
            )
            sid = cur.fetchone()[0]
            cur.execute("DELETE FROM hippocampus.document_chunks WHERE source_id = %s", (sid,))
            for ec in chunks:
                c = ec.chunk
                cur.execute(
                    """INSERT INTO hippocampus.document_chunks
                       (source_id, chunk_index, header_path, content, token_count, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s::vector)""",
                    (sid, c.index, c.header_path, c.content, c.token_count, _vec(ec.embedding)),
                )

    def delete_source(self, collection: str, uri: str) -> None:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cid = self._collection_id(cur, collection)
            cur.execute("DELETE FROM hippocampus.sources WHERE collection_id = %s AND uri = %s", (cid, uri))

    # --- read/admin surface used by the API (HIPPO-E04-F1-S01) ---

    def list_collections(self) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT k.slug, k.name, k.description, k.created_at,
                          (SELECT count(*) FROM hippocampus.sources s WHERE s.collection_id = k.id),
                          (SELECT count(*) FROM hippocampus.document_chunks c
                             JOIN hippocampus.sources s ON s.id = c.source_id WHERE s.collection_id = k.id),
                          k.visibility
                   FROM hippocampus.collections k ORDER BY k.slug"""
            )
            return [
                {"slug": r[0], "name": r[1], "description": r[2], "created_at": r[3].isoformat(),
                 "source_count": r[4], "chunk_count": r[5], "visibility": r[6]}
                for r in cur.fetchall()
            ]

    def list_sources(self, slug: str, limit: int = 200) -> list[dict] | None:
        """Newest-indexed first; None if the collection does not exist."""
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT s.uri, s.title, s.status, s.chunk_count, s.sha256_hash, s.error_message, s.last_indexed_at
                   FROM hippocampus.sources s JOIN hippocampus.collections k ON k.id = s.collection_id
                   WHERE k.slug = %s ORDER BY s.last_indexed_at DESC LIMIT %s""",
                (slug, limit),
            )
            rows = cur.fetchall()
            if not rows and not self.collection_exists(slug):
                return None
        return [
            {"uri": r[0], "title": r[1], "status": r[2], "chunk_count": r[3], "sha256": r[4][:12],
             "error": r[5], "last_indexed_at": r[6].isoformat()}
            for r in rows
        ]

    def create_collection(self, slug: str, name: str, description: str | None, visibility: str = "shared") -> bool:
        """Returns False if the slug already exists."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hippocampus.collections (slug, name, description, visibility) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (slug) DO NOTHING""",
                (slug, name, description, visibility),
            )
            return cur.rowcount == 1

    def collection_visibility(self, slug: str) -> str | None:
        """'shared' or 'restricted'; None if the collection does not exist."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT visibility FROM hippocampus.collections WHERE slug = %s", (slug,))
            row = cur.fetchone()
        return row[0] if row else None

    def update_collection(self, slug: str, name: str | None, description: str | None, visibility: str | None) -> bool:
        """Only the given fields change. False if the collection does not exist."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """UPDATE hippocampus.collections
                   SET name = COALESCE(%s, name), description = COALESCE(%s, description),
                       visibility = COALESCE(%s, visibility)
                   WHERE slug = %s""",
                (name, description, visibility, slug),
            )
            return cur.rowcount == 1

    # --- access control (HIPPO-E06-F0) ---

    def find_client(self, token_sha256: str) -> dict | None:
        """The live (not revoked) client owning this token hash; last_used_at is refreshed at most once a minute."""
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE hippocampus.api_clients SET last_used_at = NOW()
                   WHERE token_sha256 = %s AND revoked_at IS NULL
                     AND (last_used_at IS NULL OR last_used_at < NOW() - INTERVAL '1 minute')""",
                (token_sha256,),
            )
            cur.execute(
                """SELECT name, is_admin, read_grants, write_grants FROM hippocampus.api_clients
                   WHERE token_sha256 = %s AND revoked_at IS NULL""",
                (token_sha256,),
            )
            row = cur.fetchone()
        return {"name": row[0], "is_admin": row[1], "read_grants": row[2], "write_grants": row[3]} if row else None

    def list_clients(self) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT name, is_admin, read_grants, write_grants, note, created_at, last_used_at, revoked_at
                   FROM hippocampus.api_clients ORDER BY name"""
            )
            return [
                {"name": r[0], "admin": r[1], "read": r[2], "write": r[3], "note": r[4],
                 "created_at": r[5].isoformat(), "last_used_at": r[6].isoformat() if r[6] else None,
                 "revoked": r[7] is not None}
                for r in cur.fetchall()
            ]

    def create_client(self, name: str, token_sha256: str, admin: bool, read: list[str], write: list[str],
                      note: str | None) -> bool:
        """False if the name is taken (revoked clients keep their name)."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO hippocampus.api_clients (name, token_sha256, is_admin, read_grants, write_grants, note)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING""",
                (name, token_sha256, admin, read, write, note),
            )
            return cur.rowcount == 1

    def update_client(self, name: str, admin: bool | None, read: list[str] | None, write: list[str] | None,
                      note: str | None) -> bool:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """UPDATE hippocampus.api_clients
                   SET is_admin = COALESCE(%s, is_admin), read_grants = COALESCE(%s, read_grants),
                       write_grants = COALESCE(%s, write_grants), note = COALESCE(%s, note)
                   WHERE name = %s AND revoked_at IS NULL""",
                (admin, read, write, note, name),
            )
            return cur.rowcount == 1

    def revoke_client(self, name: str) -> bool:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "UPDATE hippocampus.api_clients SET revoked_at = NOW() WHERE name = %s AND revoked_at IS NULL", (name,)
            )
            return cur.rowcount == 1

    def rotate_client(self, name: str, token_sha256: str) -> bool:
        """Swap the token hash of a live client; the old token stops working at once. Grants and name are kept."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """UPDATE hippocampus.api_clients SET token_sha256 = %s, last_used_at = NULL
                   WHERE name = %s AND revoked_at IS NULL""",
                (token_sha256, name),
            )
            return cur.rowcount == 1

    def count_admins(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM hippocampus.api_clients WHERE is_admin AND revoked_at IS NULL")
            return cur.fetchone()[0]

    # --- saved sync sources ---

    _SOURCE_COLS = """s.name, s.kind, k.slug, s.settings, s.folder_collections, s.created_by, s.created_at, s.updated_at,
                      s.last_run_at, s.last_status, s.last_summary"""

    @staticmethod
    def _source_row(r) -> dict:
        return {"name": r[0], "kind": r[1], "collection": r[2], "settings": r[3], "folder_collections": r[4],
                "created_by": r[5], "created_at": r[6].isoformat(), "updated_at": r[7].isoformat(),
                "last_run_at": r[8].isoformat() if r[8] else None, "last_status": r[9], "last_summary": r[10]}

    def list_sync_sources(self) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(f"""SELECT {self._SOURCE_COLS} FROM hippocampus.sync_sources s
                            JOIN hippocampus.collections k ON k.id = s.collection_id ORDER BY s.name""")
            return [self._source_row(r) for r in cur.fetchall()]

    def get_sync_source(self, name: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(f"""SELECT {self._SOURCE_COLS} FROM hippocampus.sync_sources s
                            JOIN hippocampus.collections k ON k.id = s.collection_id WHERE s.name = %s""", (name,))
            row = cur.fetchone()
        return self._source_row(row) if row else None

    def create_sync_source(self, name: str, kind: str, collection: str, settings: dict, folder_collections: dict,
                           created_by: str) -> bool:
        """False if the name is taken."""
        from psycopg.types.json import Jsonb

        with self._conn.transaction(), self._conn.cursor() as cur:
            cid = self._collection_id(cur, collection)
            cur.execute(
                """INSERT INTO hippocampus.sync_sources (name, kind, collection_id, settings, folder_collections, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (name) DO NOTHING""",
                (name, kind, cid, Jsonb(settings), Jsonb(folder_collections), created_by),
            )
            return cur.rowcount == 1

    def update_sync_source(self, name: str, collection: str | None, settings: dict | None,
                           folder_collections: dict | None) -> bool:
        """Only the given fields change. False if there is no such source."""
        from psycopg.types.json import Jsonb

        with self._conn.transaction(), self._conn.cursor() as cur:
            cid = self._collection_id(cur, collection) if collection else None
            cur.execute(
                """UPDATE hippocampus.sync_sources
                   SET collection_id = COALESCE(%s, collection_id), settings = COALESCE(%s, settings),
                       folder_collections = COALESCE(%s, folder_collections), updated_at = NOW()
                   WHERE name = %s""",
                (cid, Jsonb(settings) if settings is not None else None,
                 Jsonb(folder_collections) if folder_collections is not None else None, name),
            )
            return cur.rowcount == 1

    def delete_sync_source(self, name: str) -> bool:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM hippocampus.sync_sources WHERE name = %s", (name,))
            return cur.rowcount == 1

    def record_sync_run(self, name: str, status: str, summary: dict) -> bool:
        """Remember how the last run ended. A failed run changes nothing else: the index is left as it was."""
        from psycopg.types.json import Jsonb

        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                """UPDATE hippocampus.sync_sources SET last_run_at = NOW(), last_status = %s, last_summary = %s
                   WHERE name = %s""",
                (status, Jsonb(summary), name),
            )
            return cur.rowcount == 1

    def delete_collection(self, slug: str) -> bool:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM hippocampus.collections WHERE slug = %s", (slug,))
            return cur.rowcount == 1

    def collection_exists(self, slug: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute("SELECT 1 FROM hippocampus.collections WHERE slug = %s", (slug,))
            return cur.fetchone() is not None

    def search(self, embedding: list[float], collections: list[str] | None, top_k: int, min_score: float) -> list[dict]:
        """Cosine search over exactly ``collections``; score = 1 - cosine distance.

        An empty list searches nothing: there is deliberately no "all collections" wildcard here, so a caller
        must pass the set it is allowed to read (the filter runs in SQL, before LIMIT).
        """
        if not collections:
            return []
        vec = _vec(embedding)
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT k.slug, s.uri, s.title, c.header_path, c.content, c.chunk_index,
                          1 - (c.embedding <=> %(v)s::vector) AS score
                   FROM hippocampus.document_chunks c
                   JOIN hippocampus.sources s ON s.id = c.source_id
                   JOIN hippocampus.collections k ON k.id = s.collection_id
                   WHERE k.slug = ANY(%(cols)s::text[])
                   ORDER BY c.embedding <=> %(v)s::vector
                   LIMIT %(n)s""",
                {"v": vec, "cols": list(collections), "n": top_k},
            )
            rows = cur.fetchall()
        return [
            {"collection": r[0], "uri": r[1], "title": r[2], "header_path": r[3],
             "content": r[4], "chunk_index": r[5], "score": float(r[6])}
            for r in rows
            if float(r[6]) >= min_score
        ]

    def totals(self, collections: list[str]) -> dict:
        """Counts over exactly ``collections`` (the caller's readable set)."""
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT count(DISTINCT k.id), count(DISTINCT s.id), count(c.id)
                   FROM hippocampus.collections k
                   LEFT JOIN hippocampus.sources s ON s.collection_id = k.id
                   LEFT JOIN hippocampus.document_chunks c ON c.source_id = s.id
                   WHERE k.slug = ANY(%s::text[])""",
                (list(collections),),
            )
            c, s, ch = cur.fetchone()
        return {"collections": c, "sources": s, "chunks": ch}


def connect_store(dsn: str):
    """Context manager: one short-lived autocommit connection per request, yielding a PgStore."""
    from contextlib import contextmanager

    import psycopg

    @contextmanager
    def _cm():
        with psycopg.connect(dsn, autocommit=True) as conn:
            yield PgStore(conn)

    return _cm
