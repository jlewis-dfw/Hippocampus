"""Needs a migrated hippocampus DB. Set HIPPO_TEST_DSN; uses a throwaway collection."""
import os
import uuid

import pytest

from hippocampus.diff import Document, sync_collection

pytestmark = pytest.mark.skipif(not os.environ.get("HIPPO_TEST_DSN"), reason="set HIPPO_TEST_DSN")


class FakeEmbedder:
    def embed(self, texts):
        return [[0.1] * 384 for _ in texts]


@pytest.fixture
def conn_and_slug():
    import psycopg

    slug = f"test-{uuid.uuid4().hex[:12]}"
    with psycopg.connect(os.environ["HIPPO_TEST_DSN"], autocommit=True) as conn:
        conn.execute("INSERT INTO hippocampus.collections (slug, name) VALUES (%s, %s)", (slug, slug))
        try:
            yield conn, slug
        finally:
            conn.execute("DELETE FROM hippocampus.collections WHERE slug = %s", (slug,))


def _chunk_count(conn, slug):
    return conn.execute(
        """SELECT count(*) FROM hippocampus.document_chunks c
           JOIN hippocampus.sources s ON s.id = c.source_id
           JOIN hippocampus.collections k ON k.id = s.collection_id WHERE k.slug = %s""",
        (slug,),
    ).fetchone()[0]


def test_unchanged_changed_deleted(conn_and_slug):
    from hippocampus.store import PgStore

    conn, slug = conn_and_slug
    store, emb = PgStore(conn), FakeEmbedder()
    docs = {"a.md": Document("A", "# A\none"), "b.md": Document("B", "# B\ntwo\n\n## C\nthree")}

    r = sync_collection(slug, docs, store, emb)
    assert sorted(r.added) == ["a.md", "b.md"]
    assert _chunk_count(conn, slug) == 3

    r = sync_collection(slug, docs, store, emb)
    assert sorted(r.unchanged) == ["a.md", "b.md"] and not r.changed

    docs["b.md"] = Document("B", "# B\nonly one chunk now")
    r = sync_collection(slug, docs, store, emb)
    assert r.changed == ["b.md"] and _chunk_count(conn, slug) == 2

    del docs["a.md"]
    r = sync_collection(slug, docs, store, emb)
    assert r.deleted == ["a.md"] and _chunk_count(conn, slug) == 1


def test_api_read_surface(conn_and_slug):
    """list_collections / search / totals against real pgvector (HIPPO-E04-F1-S01)."""
    from hippocampus.store import PgStore

    class Emb:
        def embed(self, texts):
            return [([1.0] + [0.0] * 383) if "alpha" in t else ([0.0, 1.0] + [0.0] * 382) for t in texts]

    conn, slug = conn_and_slug
    store = PgStore(conn)
    sync_collection(slug, {"a.md": Document("A", "# A\nalpha text"), "b.md": Document("B", "# B\nbeta text")}, store, Emb())

    mine = next(c for c in store.list_collections() if c["slug"] == slug)
    assert mine["source_count"] == 2 and mine["chunk_count"] == 2

    hits = store.search([1.0] + [0.0] * 383, [slug], 5, 0.0)
    assert [h["uri"] for h in hits] == ["a.md", "b.md"]
    assert hits[0]["score"] > 0.99 and hits[1]["score"] < 0.01
    assert [h["uri"] for h in store.search([1.0] + [0.0] * 383, [slug], 5, 0.5)] == ["a.md"]
    assert store.search([1.0] + [0.0] * 383, ["no-such-collection"], 5, 0.0) == []
    assert store.totals([slug])["chunks"] == 2
    sources = store.list_sources(slug)
    assert sorted(x["uri"] for x in sources) == ["a.md", "b.md"] and sources[0]["status"] == "indexed"
    assert len(sources[0]["sha256"]) == 12 and sources[0]["chunk_count"] == 1
    assert store.list_sources("no-such-collection") is None
    assert store.create_collection(slug, "dup", None) is False


def test_restricted_filtering_happens_before_limit(conn_and_slug):
    """A hidden collection that out-ranks everything must not shrink or leak into a visible result set."""
    import uuid

    from hippocampus.store import PgStore

    class Emb:
        def embed(self, texts):
            return [([1.0] + [0.0] * 383) if "alpha" in t else ([0.0, 1.0] + [0.0] * 382) for t in texts]

    conn, shared = conn_and_slug
    hidden = f"test-{uuid.uuid4().hex[:12]}"
    store = PgStore(conn)
    assert store.create_collection(hidden, "hidden", None, "restricted")
    try:
        assert store.collection_visibility(shared) == "shared" and store.collection_visibility(hidden) == "restricted"
        assert store.collection_visibility("no-such-collection") is None
        sync_collection(shared, {"s.md": Document("S", "# S\nbeta only")}, store, Emb())
        sync_collection(hidden, {f"h{i}.md": Document("H", f"# H\nalpha {i}") for i in range(3)}, store, Emb())

        hits = store.search([1.0] + [0.0] * 383, [shared], 1, 0.0)
        assert [h["collection"] for h in hits] == [shared]          # top_k=1 still returns the visible chunk
        assert store.search([1.0] + [0.0] * 383, [], 5, 0.0) == []   # no wildcard
        assert store.totals([shared])["chunks"] == 1
        assert store.update_collection(hidden, "renamed", None, "shared") is True
        assert store.collection_visibility(hidden) == "shared"
    finally:
        store.delete_collection(hidden)


def test_client_lifecycle(conn_and_slug):
    from hippocampus import auth
    from hippocampus.store import PgStore

    conn, slug = conn_and_slug
    store = PgStore(conn)
    name = f"t-{slug[-8:]}"
    token = auth.new_token()
    try:
        assert store.create_client(name, auth.hash_token(token), False, ["a-*"], ["b"], "note") is True
        assert store.create_client(name, auth.hash_token(auth.new_token()), False, [], [], None) is False
        assert auth.authenticate(f"Bearer {token}", store) == auth.Principal(name, False, ["a-*"], ["b"])
        assert store.update_client(name, True, None, ["c"], None) is True
        me = auth.authenticate(f"Bearer {token}", store)
        assert me.admin and me.read == ["a-*"] and me.write == ["c"]
        listed = next(c for c in store.list_clients() if c["name"] == name)
        assert listed["last_used_at"] and not listed["revoked"] and "token" not in str(listed)
        newer = auth.new_token()
        assert store.rotate_client(name, auth.hash_token(newer)) is True
        with pytest.raises(auth.AuthError):  # the old token died with the rotation
            auth.authenticate(f"Bearer {token}", store)
        assert auth.authenticate(f"Bearer {newer}", store).write == ["c"]
        token = newer
        assert store.rotate_client("no-such-client", auth.hash_token(auth.new_token())) is False
        assert store.revoke_client(name) is True and store.revoke_client(name) is False
        assert store.rotate_client(name, auth.hash_token(auth.new_token())) is False  # revoked clients stay revoked
        with pytest.raises(auth.AuthError):
            auth.authenticate(f"Bearer {token}", store)
    finally:
        conn.execute("DELETE FROM hippocampus.api_clients WHERE name = %s", (name,))
