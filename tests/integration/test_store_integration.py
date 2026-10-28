"""Runs against a migrated hippocampus database: a throwaway container by default, or the one HIPPO_TEST_DSN names."""
import os
import uuid

import pytest

from hippocampus.core.diff import Document, sync_collection

pytestmark = pytest.mark.usefixtures("database")  # a throwaway database unless HIPPO_TEST_DSN names one (see conftest.py)


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
    from hippocampus.storage.store import PgStore

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
    from hippocampus.storage.store import PgStore

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

    from hippocampus.storage.store import PgStore

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
    from hippocampus.service import auth
    from hippocampus.storage.store import PgStore

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


def test_unknown_collection_and_admin_count(conn_and_slug):
    from hippocampus.service import auth
    from hippocampus.storage.store import PgStore

    conn, slug = conn_and_slug
    store = PgStore(conn)
    with pytest.raises(LookupError, match="does not exist"):
        store.replace_source(f"missing-{slug}", "u", None, "sha", [])
    name = f"adm-{slug[-8:]}"
    before = store.count_admins()
    try:
        assert store.create_client(name, auth.hash_token(auth.new_token()), True, [], [], None) is True
        assert store.count_admins() == before + 1
        assert store.revoke_client(name) is True
        assert store.count_admins() == before  # revoked admins do not count
    finally:
        conn.execute("DELETE FROM hippocampus.api_clients WHERE name = %s", (name,))


def test_sync_source_lifecycle(conn_and_slug):
    from hippocampus.storage.store import PgStore

    conn, slug = conn_and_slug
    store = PgStore(conn)
    other = f"{slug}-b"
    conn.execute("INSERT INTO hippocampus.collections (slug, name) VALUES (%s, %s)", (other, other))
    name = f"src-{slug[-8:]}"
    settings = {"include": ["Work/"], "folders": ["Work"], "extensions": None}
    try:
        assert store.create_sync_source(name, "upload", slug, settings, {"Work": other}, "tester") is True
        assert store.create_sync_source(name, "upload", slug, {}, {}, "tester") is False  # name taken
        src = store.get_sync_source(name)
        assert (src["collection"], src["kind"], src["settings"], src["folder_collections"]) == (
            slug, "upload", settings, {"Work": other})
        assert src["last_status"] is None and src["last_run_at"] is None
        assert name in [s["name"] for s in store.list_sync_sources()]

        assert store.update_sync_source(name, None, {"folders": ["Projects"]}, None) is True
        assert store.get_sync_source(name)["settings"] == {"folders": ["Projects"]}
        assert store.get_sync_source(name)["folder_collections"] == {"Work": other}  # untouched
        assert store.update_sync_source(name, other, None, {}) is True
        assert store.get_sync_source(name)["collection"] == other
        assert store.update_sync_source("missing", None, {}, None) is False

        assert store.record_sync_run(name, "ok", {"added": 2}) is True
        src = store.get_sync_source(name)
        assert (src["last_status"], src["last_summary"]) == ("ok", {"added": 2}) and src["last_run_at"]
        assert store.record_sync_run("missing", "ok", {}) is False
        with pytest.raises(Exception, match="ck_sync_sources_status"):
            store.record_sync_run(name, "bogus", {})

        conn.execute("DELETE FROM hippocampus.collections WHERE slug = %s", (other,))  # cascades
        assert store.get_sync_source(name) is None
        assert store.create_sync_source(name, "upload", slug, {}, {}, "tester") is True
        assert store.delete_sync_source(name) is True and store.delete_sync_source(name) is False
    finally:
        conn.execute("DELETE FROM hippocampus.sync_sources WHERE name = %s", (name,))
        conn.execute("DELETE FROM hippocampus.collections WHERE slug = %s", (other,))


def test_pool_second_caller_waits_and_a_third_times_out():
    import threading
    import time

    from hippocampus.storage.store import StoreBusy, connect_store

    factory = connect_store(os.environ["HIPPO_TEST_DSN"], pool=(1, 1, 1.0))
    holder = factory()
    first = holder.__enter__()
    threading.Timer(0.3, lambda: holder.__exit__(None, None, None)).start()
    started = time.monotonic()
    with factory() as second:  # waits for the only connection, then gets it
        assert second.totals([]) == {"collections": 0, "sources": 0, "chunks": 0}
    assert 0.2 < time.monotonic() - started < 1.0 and first is not None

    short = connect_store(os.environ["HIPPO_TEST_DSN"], pool=(1, 1, 0.3))
    with short() as _held:
        with pytest.raises(StoreBusy):
            with short():
                pass
    with short() as again:  # the pool recovers once the connection is returned
        assert again.totals([]) is not None
