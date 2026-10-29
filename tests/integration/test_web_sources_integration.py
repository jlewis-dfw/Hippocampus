"""Saved web sources and the collection view over the real database: the API on PgStore, a shared page, and an upgrade."""
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("database")  # a throwaway database unless HIPPO_TEST_DSN names one (see conftest.py)

ROOT = Path(__file__).resolve().parents[2]
SITE = {  # a public literal, so the crawler's address guard lets it through; served by the fake fetcher
    "https://93.184.216.34/": "<main><h1>Home</h1><p>start</p><a href='/a'>A</a></main>",
    "https://93.184.216.34/a": "<main><h1>Alpha</h1><p>level two</p><a href='/c'>C</a></main>",
    "https://93.184.216.34/c": "<main><h1>C</h1><p>level three</p></main>",
}
START = "https://93.184.216.34/"


class FakeEmbedder:
    def embed(self, texts):
        return [[0.1] * 384 for _ in texts]


class Fetcher:
    def fetch(self, url):
        from hippocampus.ingest.crawler import FetchResult

        return FetchResult(url, 200, "text/html", SITE[url]) if url in SITE else FetchResult(url, 404, "text/html", "")

    def fetch_robots(self, url):
        return None


@pytest.fixture
def world():
    """The real app over the real store, with a crawler that reads SITE; yields (client, store factory, admin headers, slug)."""
    import psycopg
    from fastapi.testclient import TestClient

    from hippocampus.ingest.crawler import make_crawler
    from hippocampus.service import auth
    from hippocampus.service.api import create_app
    from hippocampus.service.jobs import JobRegistry
    from hippocampus.storage.store import PgStore

    dsn = os.environ["HIPPO_TEST_DSN"]

    @contextmanager
    def factory():
        with psycopg.connect(dsn, autocommit=True) as conn:
            yield PgStore(conn)

    tag = uuid.uuid4().hex[:8]
    slug = f"test-web-{tag}"
    with factory() as store:
        store.create_client(f"root-{tag}", auth.hash_token(f"hip_root_{tag}"), True, [], [], None)
        store.create_client(f"reader-{tag}", auth.hash_token(f"hip_reader_{tag}"), False, [slug], [], None)
        store.create_client(f"nobody-{tag}", auth.hash_token(f"hip_nobody_{tag}"), False, [], [], None)
    crawler = make_crawler(factory, FakeEmbedder(), Fetcher(), delay=0, sleep=lambda s: None)
    client = TestClient(create_app(factory, FakeEmbedder(), JobRegistry(), crawler, require_auth=True))
    heads = {n: {"Authorization": f"Bearer hip_{n}_{tag}"} for n in ("root", "reader", "nobody")}
    assert client.post("/v1/collections", json={"slug": slug, "name": "Web", "visibility": "restricted"},
                       headers=heads["root"]).status_code == 201
    try:
        yield client, factory, heads, slug
    finally:
        with factory() as store:
            store.delete_collection(slug)
            for n in ("root", "reader", "nobody"):
                store.revoke_client(f"{n}-{tag}")


def wait(client, job_id, headers):
    for _ in range(300):
        job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_crawl_save_change_depth_prune_and_remove_over_the_real_store(world):
    client, _, heads, slug = world
    root = heads["root"]
    job = wait(client, client.post("/v1/crawl", json={"url": START, "collection": slug, "depth": 3}, headers=root).json()["job_id"], root)
    assert job["status"] == "done", job
    assert "depths" not in job["result"] and job["result"]["pages"] == 3

    (web,) = client.get("/v1/web-sources", headers=root).json()["web_sources"]
    assert (web["url"], web["depth"], web["pages"], web["deepest"], web["last_status"]) == (START, 3, 3, 3, "ok")
    rows = {r["uri"]: r for r in client.get(f"/v1/collections/{slug}/sources", headers=root).json()["sources"]}
    assert [rows[u]["crawl_depth"] for u in SITE] == [1, 2, 3] and {r["type"] for r in rows.values()} == {"web"}
    assert next(c for c in client.get("/v1/collections", headers=root).json()["collections"] if c["slug"] == slug)["source_types"] == ["web"]

    # change the depth, re-crawl with the saved settings (nothing changed, so every page is skipped), then prune
    assert client.patch(f"/v1/web-sources/{web['id']}", json={"depth": 2}, headers=root).json()["depth"] == 2
    again = wait(client, client.post(f"/v1/web-sources/{web['id']}/crawl", headers=root).json()["job_id"], root)
    assert again["result"]["pages"] == 2 and again["result"]["unchanged"] == 2  # depth 2 reaches the first two pages only
    assert client.post(f"/v1/web-sources/{web['id']}/prune", json={"depth": 2}, headers=root).json()["pages"] == 1
    assert client.post(f"/v1/web-sources/{web['id']}/prune", json={"depth": 2, "dry_run": False}, headers=root).json()["pages"] == 1
    left = [r["uri"] for r in client.get(f"/v1/collections/{slug}/sources", headers=root).json()["sources"]]
    assert sorted(left) == sorted(list(SITE)[:2])

    # remove one source from the collection view
    out = client.request("DELETE", f"/v1/collections/{slug}/sources", json={"uris": [START]}, headers=root).json()
    assert out["removed"] == 1
    assert client.get("/v1/web-sources", headers=root).json()["web_sources"][0]["pages"] == 1
    # forgetting the saved crawl keeps its page
    assert client.delete(f"/v1/web-sources/{web['id']}", headers=root).status_code == 204
    assert len(client.get(f"/v1/collections/{slug}/sources", headers=root).json()["sources"]) == 1


def test_web_source_access_rules_over_the_real_store(world):
    client, _, heads, slug = world
    root, reader, nobody = heads["root"], heads["reader"], heads["nobody"]
    wait(client, client.post("/v1/crawl", json={"url": START, "collection": slug, "depth": 2}, headers=root).json()["job_id"], root)
    web_id = client.get("/v1/web-sources", headers=root).json()["web_sources"][0]["id"]
    assert client.get("/v1/web-sources", headers=nobody).json()["web_sources"] == []
    assert len(client.get("/v1/web-sources", headers=reader).json()["web_sources"]) == 1
    sources = f"/v1/collections/{slug}/sources"
    calls = [("patch", f"/v1/web-sources/{web_id}", {"depth": 1}), ("post", f"/v1/web-sources/{web_id}/crawl", None),
             ("post", f"/v1/web-sources/{web_id}/prune", {"depth": 1, "dry_run": False}),
             ("delete", f"/v1/web-sources/{web_id}", None), ("delete", sources, {"uris": [START]})]
    for who, status in ((nobody, 404), (reader, 403)):  # hidden looks missing; a reader may not write
        for verb, path, body in calls:
            kwargs = {"json": body} if body else {}
            r = client.request(verb.upper(), path, headers=who, **kwargs)
            assert r.status_code == status, (verb, path, r.status_code)
    assert len(client.get(sources, headers=root).json()["sources"]) == 2  # nothing was removed


def test_a_page_reached_by_two_crawls_belongs_to_the_one_that_ran_last(world):
    _, factory, _, slug = world
    from hippocampus.core.diff import Document, sync_collection

    shared = "https://shared.example/page"
    with factory() as store:
        sync_collection(slug, {shared: Document("S", "# S\nshared")}, store, FakeEmbedder(), prune=False)
        first = store.upsert_web_source(slug, "https://shared.example/one/", 3, None, "static", "t")
        second = store.upsert_web_source(slug, "https://shared.example/two/", 3, None, "static", "t")
        store.tag_web_pages(slug, first, {shared: 3})
        store.tag_web_pages(slug, second, {shared: 3})  # the later crawl takes it over
        assert store.prune_web_source(first, 1) == 0  # the first crawl no longer owns it, so it cannot remove it
        assert store.prune_web_source(second, 1, dry_run=True) == 1
        assert store.prune_web_source(second, 1) == 1


def test_upgrading_keeps_old_web_pages_and_never_prunes_them(tmp_path):
    """The state of a database crawled before saved crawls existed: pages, no saved crawl, no depths."""
    import psycopg
    from psycopg import conninfo, sql

    from hippocampus.core.diff import Document, sync_collection
    from hippocampus.storage.migrate import apply_all
    from hippocampus.storage.store import PgStore

    dsn = os.environ["HIPPO_TEST_DSN"]
    name = f"hippo_upgrade_test_{uuid.uuid4().hex[:8]}"
    admin = conninfo.make_conninfo(dsn, dbname="postgres")
    old = tmp_path / "old"
    old.mkdir()
    for f in sorted((ROOT / "db" / "migrations").glob("*.sql"))[:3]:  # the schema before 0004
        shutil.copy(f, old / f.name)
    with psycopg.connect(admin, autocommit=True) as a:
        a.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(conninfo.make_conninfo(dsn, dbname=name), autocommit=True) as conn:
            apply_all(conn, old)
            assert conn.execute("SELECT to_regclass('hippocampus.web_sources')").fetchone()[0] is None
            conn.execute("INSERT INTO hippocampus.collections (slug, name) VALUES ('python-tutorial', 'Python Tutorial')")
            sync_collection("python-tutorial", {"https://docs.python.org/3/tutorial/index.html": Document("T", "# T\ntutorial"),
                                                "https://docs.python.org/3/tutorial/appendix.html": Document("A", "# A\nappendix")},
                            PgStore(conn), FakeEmbedder(), prune=False)
            assert apply_all(conn)[-1].startswith("0004")  # the migration runs over data that is already there
            store = PgStore(conn)
            assert store.list_web_sources() == []  # nothing saved yet
            (coll,) = store.list_collections()
            assert (coll["source_count"], coll["source_types"]) == (2, ["web"])
            rows = store.list_sources("python-tutorial")
            assert [(r["crawl_depth"], r["web_source_id"]) for r in rows] == [(None, None)] * 2
            # adopting it: crawling the same start URL saves a crawl, and pruning never touches the untagged pages
            web_id = store.upsert_web_source("python-tutorial", "https://docs.python.org/3/tutorial/", 2, None, "static", "t")
            assert store.prune_web_source(web_id, 1) == 0 and len(store.list_sources("python-tutorial")) == 2
    finally:
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
