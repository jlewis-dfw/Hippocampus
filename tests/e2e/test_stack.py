"""The whole product through its real surfaces: HTTP API, admin CLI, MCP stdio server, database and embedding model.

The tests share one stack and run in file order; later ones use what earlier ones indexed.
"""
import asyncio
import os
import sys
import time
import uuid

import pytest
from e2e_support import ROOT, Api

PG_DOC = ("# Database backups\n\nRun pg_dump every night and copy the dump to another machine. "
          "Restore a PostgreSQL database with pg_restore after stopping the application.")
CAT_DOC = "# Cats\n\nCats are small domesticated felines. They purr, sleep for most of the day and chase mice."
SLUG = "e2e-docs"


@pytest.fixture(scope="module")
def docs(admin):
    """The shared collection the tests index into."""
    assert admin.post("/v1/collections", json={"slug": SLUG, "name": "E2E docs"}).status_code == 201
    return SLUG


def wait_job(api: Api, job_id: str, timeout: int = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = api.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.5)
    raise AssertionError(f"job {job_id} did not finish")


# --- the service is up

def test_status_reports_database_and_model(anon):
    body = anon.get("/v1/status").json()
    assert body["embedder"] == {"model": "BAAI/bge-small-en-v1.5", "dimensions": 384}
    assert body["db_latency_ms"] >= 0


def test_dashboard_is_served_with_a_strict_policy(anon):
    r = anon.get("/", follow_redirects=True)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "default-src" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_admin_cli_token_authenticates(admin):
    who = admin.get("/v1/whoami").json()
    assert who["name"] == "e2e-root" and who["admin"] is True and who["require_auth"] is True


# --- authentication is enforced

def test_anonymous_can_not_write(anon, docs):
    assert anon.post("/v1/collections", json={"slug": "nope", "name": "Nope"}).status_code in (401, 403)
    assert anon.post("/v1/ingest/file", json={"collection": SLUG, "uri": "a.md", "text": "x"}).status_code in (401, 403)


def test_bad_token_is_rejected_not_treated_as_anonymous(stack):
    assert Api(stack.url, "hip_not_a_real_token").get("/v1/whoami").status_code == 401


# --- index and search with the real model

def test_index_then_find_by_meaning(admin, anon, docs):
    for uri, text in (("pg.md", PG_DOC), ("cats.md", CAT_DOC)):
        r = admin.post("/v1/ingest/file", json={"collection": SLUG, "uri": uri, "text": text})
        assert r.status_code == 200 and r.json()["added"] == [uri]
    # no word of the query appears in the document: only meaning can match
    hits = anon.post("/v1/search", json={"query": "how do we keep copies of our data safe?", "collections": [SLUG]})
    results = hits.json()["results"]
    assert results[0]["uri"] == "pg.md" and results[0]["collection"] == SLUG
    cat = anon.post("/v1/search", json={"query": "a purring animal", "collections": [SLUG], "top_k": 1}).json()
    assert cat["results"][0]["uri"] == "cats.md"


def test_reindexing_is_incremental(admin):
    same = admin.post("/v1/ingest/file", json={"collection": SLUG, "uri": "pg.md", "text": PG_DOC}).json()
    assert same["unchanged"] == ["pg.md"] and same["added"] == [] and same["changed"] == []
    edited = admin.post("/v1/ingest/file", json={"collection": SLUG, "uri": "pg.md", "text": PG_DOC + "\n\nMore."}).json()
    assert edited["changed"] == ["pg.md"]


def test_sources_and_status_counts(admin):
    sources = admin.get(f"/v1/collections/{SLUG}/sources").json()["sources"]
    assert {s["uri"] for s in sources} == {"pg.md", "cats.md"} and all(s["chunk_count"] >= 1 for s in sources)
    assert admin.get("/v1/status").json()["sources"] >= 2


def test_excluded_sources_are_refused(admin):
    r = admin.post("/v1/ingest/file", json={"collection": SLUG, "uri": "private-infra/x.md", "text": "10.0.0.1"})
    assert r.status_code == 403


# --- restricted collections, scoped clients, rotation, revocation

def test_restricted_collection_is_invisible_without_a_grant(stack, admin, anon):
    slug = f"e2e-private-{uuid.uuid4().hex[:6]}"
    assert admin.post("/v1/collections", json={"slug": slug, "name": "P", "visibility": "restricted"}).status_code == 201
    text = "# Vault\n\nThe launch codes are stored in the zebra safe."
    assert admin.post("/v1/ingest/file", json={"collection": slug, "uri": "v.md", "text": text}).status_code == 200

    # anonymous: not in the list, not searchable, and the name answers 404 (it does not confirm the collection exists)
    assert slug not in [c["slug"] for c in anon.get("/v1/collections").json()["collections"]]
    leak = anon.post("/v1/search", json={"query": "launch codes zebra safe"}).json()["results"]
    assert all(h["collection"] != slug for h in leak)
    assert anon.post("/v1/search", json={"query": "launch codes", "collections": [slug]}).status_code == 404
    assert anon.get(f"/v1/collections/{slug}/sources").status_code == 404

    # a reader sees it but can not write; a writer can
    created = admin.post("/v1/clients", json={"name": "e2e-reader", "read": [slug]})
    assert created.status_code == 201
    reader = Api(stack.url, created.json()["token"])
    found = reader.post("/v1/search", json={"query": "where are the launch codes", "collections": [slug]}).json()
    assert found["results"][0]["uri"] == "v.md"
    assert reader.post("/v1/ingest/file", json={"collection": slug, "uri": "w.md", "text": "x"}).status_code in (403, 404)
    assert reader.get("/v1/clients").status_code == 403  # not an admin

    # rotation: the old token dies at once, the new one works
    old = created.json()["token"]
    new = admin.post("/v1/clients/e2e-reader/rotate").json()["token"]
    assert Api(stack.url, old).get("/v1/whoami").status_code == 401
    assert Api(stack.url, new).get("/v1/whoami").json()["name"] == "e2e-reader"

    # revocation
    assert admin.delete("/v1/clients/e2e-reader").status_code == 204
    assert Api(stack.url, new).get("/v1/whoami").status_code == 401

    # deleting the collection removes its content for good
    assert admin.delete(f"/v1/collections/{slug}").status_code == 204
    assert admin.post("/v1/search", json={"query": "launch codes", "collections": [slug]}).status_code == 404


def test_the_last_admin_can_not_be_removed(admin):
    assert admin.delete("/v1/clients/e2e-root").status_code == 409
    assert admin.patch("/v1/clients/e2e-root", json={"admin": False}).status_code == 409


# --- crawler guard and jobs

def test_crawl_of_a_private_address_fails_the_job(admin):
    r = admin.post("/v1/crawl", json={"url": "http://127.0.0.1:9/", "collection": SLUG})
    assert r.status_code == 202
    job = wait_job(admin, r.json()["job_id"])
    assert job["status"] == "error" and "non-public" in job["error"]
    events = admin.get(f"/v1/jobs/{r.json()['job_id']}/events").text
    assert "event: end" in events


def test_job_belongs_to_its_owner(stack, admin, anon):
    job_id = admin.post("/v1/crawl", json={"url": "http://127.0.0.1:9/", "collection": SLUG}).json()["job_id"]
    wait_job(admin, job_id)
    assert anon.get(f"/v1/jobs/{job_id}").status_code == 404


# --- MCP server over stdio, talking to the live API

def test_mcp_search_tool_end_to_end(stack, admin_token):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "hippocampus.cli.mcp_server"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "HIPPO_URL": stack.url, "HIPPO_TOKEN": admin_token})

    async def run():
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            res = await session.call_tool("hippocampus_search",
                                          {"query": "restoring the database from a dump", "collection": SLUG})
            return tools, "\n".join(c.text for c in res.content)

    tools, text = asyncio.run(asyncio.wait_for(run(), timeout=120))
    assert tools == {"hippocampus_search", "hippocampus_index_doc"}
    assert "pg.md" in text and "pg_restore" in text


# --- durability: runs last because it restarts the API

def test_data_survives_an_api_restart(stack, anon):
    res = stack.compose("restart", "api")
    assert res.returncode == 0, res.stderr
    stack.wait_ready(timeout=300)
    hits = anon.post("/v1/search", json={"query": "nightly database dumps", "collections": [SLUG]}).json()["results"]
    assert hits and hits[0]["uri"] == "pg.md"
