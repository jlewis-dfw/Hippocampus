"""Smaller branches across api, docs_sync, mcp_server, jobs and chunker that the feature tests don't reach."""
import asyncio
import hashlib
import hmac
import io
import time
import urllib.error
from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from test_access import add_client, build
from test_api import KeywordEmbedder, MemStore, make_client
from test_docs_sync import FakeSource, finished, push, setup

from hippocampus import api, chunker, docs_sync, mcp_server
from hippocampus.api import create_app
from hippocampus.docs_sync import DocsSyncConfig, ForgejoSource
from hippocampus.jobs import JobRegistry

# --- api


def test_sources_of_a_missing_collection_is_404():
    c, _ = make_client()
    assert c.get("/v1/collections/nope/sources").status_code == 404


def test_update_client_errors():
    c, mem = build()
    admin = add_client(mem, "root", "hip_root", admin=True)
    assert c.patch("/v1/clients/root", json={"read": ["*"]}, headers=admin).status_code == 422
    assert c.patch("/v1/clients/ghost", json={"note": "x"}, headers=admin).status_code == 404
    assert c.patch("/v1/clients/root", json={"admin": False}, headers=admin).status_code == 409


def test_crawl_refuses_excluded_urls():
    c, mem = make_client(crawler=lambda job, req: {})
    mem.create_collection("web", "web", None)
    r = c.post("/v1/crawl", json={"url": "https://docs.example/private-infra/x", "collection": "web"})
    assert r.status_code == 403


def test_event_stream_waits_for_a_running_job():
    def slow(job, req):
        time.sleep(0.4)
        job.log("late line")
        return {"pages": 0}

    c, mem = make_client(crawler=slow)
    mem.create_collection("web", "web", None)
    job = c.post("/v1/crawl", json={"url": "https://93.184.216.34/", "collection": "web"}).json()["job_id"]
    body = c.get(f"/v1/jobs/{job}/events").text
    assert "late line" in body and "event: end" in body


def test_webhook_payload_problems():
    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    cfg = DocsSyncConfig("docs", "acme-org", "docs-repo", "main", "hook-secret", FakeSource({}))
    c = TestClient(create_app(factory, KeywordEmbedder(), JobRegistry(), docs_sync=cfg))

    def signed(body: bytes):
        sig = hmac.new(b"hook-secret", body, hashlib.sha256).hexdigest()
        return c.post("/v1/webhooks/forgejo", content=body, headers={"X-Forgejo-Signature": sig})

    assert signed(b"not json").status_code == 422
    assert "ignored" in signed(b"[]").json()
    commits = [{"added": ["a.md"], "modified": [], "removed": []}]
    assert push(c, commits).status_code == 404  # the docs collection does not exist yet


def test_app_from_env_wires_everything(monkeypatch):
    from hippocampus import embedder as embedder_mod
    from hippocampus import store as store_mod

    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    seen = {}
    monkeypatch.setattr(store_mod, "connect_store", lambda dsn, pool=None: seen.update(dsn=dsn, pool=pool) or factory)
    monkeypatch.setattr(embedder_mod, "FastEmbedder", lambda cache_dir=None: KeywordEmbedder())
    for k, v in {"HIPPO_DSN": "postgresql://x", "HIPPO_CRAWL_MAX_PAGES": "5", "HIPPO_CRAWL_DELAY": "0",
                 "HIPPO_REQUIRE_AUTH": "true", "HIPPO_ALLOWED_CIDRS": "10.0.0.0/8", "HIPPO_FORGEJO_URL": "https://forge.example",
                 "HIPPO_FORGEJO_TOKEN": "tok", "HIPPO_WEBHOOK_SECRET": "s3cret"}.items():
        monkeypatch.setenv(k, v)
    client = TestClient(api.app_from_env(), client=("10.1.2.3", 50000))
    assert seen["dsn"] == "postgresql://x" and seen["pool"] == (1, 5, 5.0)  # the default pool
    assert client.get("/v1/status").status_code == 200


def test_docs_sync_from_env(monkeypatch):
    for k in ("HIPPO_FORGEJO_URL", "HIPPO_FORGEJO_TOKEN", "HIPPO_WEBHOOK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    assert api._docs_sync_from_env() is None
    monkeypatch.setenv("HIPPO_FORGEJO_URL", "https://forge.example/")
    monkeypatch.setenv("HIPPO_FORGEJO_TOKEN", "tok")
    monkeypatch.setenv("HIPPO_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("HIPPO_DOCS_REPO", "my-docs")
    cfg = api._docs_sync_from_env()
    assert cfg.repo == "my-docs" and cfg.collection == "docs" and cfg.branch == "main"


# --- docs_sync


def test_apply_changes_drops_missing_and_oversized_files(monkeypatch):
    c, mem, source, _ = setup({"a.md": "# A\n\ncats", "big.md": "# Big\n\n" + "word " * 50})
    finished(c, c.post("/v1/sync/docs"))
    monkeypatch.setattr(docs_sync, "MAX_FILE_BYTES", 100)
    del source.files["a.md"]  # fetch now answers "not found"
    res = finished(c, push(c, [{"added": [], "modified": ["a.md", "big.md"], "removed": []}]))
    assert res["rejected"] == [{"path": "big.md", "reasons": ["too-large"]}]
    assert res["removed"] == 2 and mem.collections["docs"] == {}


def _forgejo(handler):
    src = ForgejoSource("https://forge.example/", "acme org", "docs", "tok")
    src._client = httpx.Client(base_url="https://forge.example/api/v1", transport=httpx.MockTransport(handler))
    return src


def test_forgejo_source_lists_all_pages_of_the_tree():
    def handler(req):
        assert "acme%20org/docs" in str(req.url)
        page = int(req.url.params["page"])
        tree = [{"path": f"p{page}.md", "type": "blob"}, {"path": "dir", "type": "tree"}]
        return httpx.Response(200, json={"tree": tree, "truncated": page < 2})

    assert _forgejo(handler).list_files("main") == ["p1.md", "p2.md"]


def test_forgejo_source_reads_files():
    def handler(req):
        if req.url.path.endswith("gone.md"):
            return httpx.Response(404)
        if req.url.path.endswith("broken.md"):
            return httpx.Response(500)
        return httpx.Response(200, content="héllo".encode())

    src = _forgejo(handler)
    assert src.read_file("a.md", "main") == "héllo"
    assert src.read_file("gone.md", "main") is None
    with pytest.raises(httpx.HTTPStatusError):
        src.read_file("broken.md", "main")


# --- mcp_server


def test_call_needs_a_base_url(monkeypatch):
    monkeypatch.delenv("HIPPO_URL", raising=False)
    with pytest.raises(RuntimeError, match="HIPPO_URL"):
        mcp_server._call("GET", "/x")


def test_call_reports_http_errors(monkeypatch):
    monkeypatch.setenv("HIPPO_URL", "http://api.example")

    def boom(*a, **k):
        raise urllib.error.HTTPError("http://api.example/x", 500, "err", {}, io.BytesIO(b"kaput"))

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="500: kaput"):
        mcp_server._call("GET", "/x")


def test_search_with_no_hits(monkeypatch):
    monkeypatch.setattr(mcp_server, "_call", lambda *a, **k: {"results": []})
    assert mcp_server.search("q") == "No matching chunks."


def test_index_doc_refuses_large_files(monkeypatch, tmp_path):
    f = tmp_path / "big.md"
    f.write_text("0123456789")
    monkeypatch.setattr(mcp_server, "MAX_FILE_BYTES", 3)
    assert "larger than" in mcp_server.index_doc(str(f))


def test_index_doc_reports_states(monkeypatch, tmp_path):
    f = tmp_path / "n.md"
    f.write_text("# n")
    for out, word in (({"added": ["u"], "changed": []}, "added"), ({"added": [], "changed": ["u"]}, "changed"),
                      ({"added": [], "changed": []}, "unchanged")):
        monkeypatch.setattr(mcp_server, "_call", lambda *a, _o=out, **k: _o)
        assert word in mcp_server.index_doc(str(f), "notes")


def test_tools_turn_api_errors_into_text(monkeypatch, tmp_path):
    def down(*a, **k):
        raise RuntimeError("api is down")

    monkeypatch.setattr(mcp_server, "_call", down)
    monkeypatch.delenv("HIPPO_MCP_READONLY", raising=False)
    server = mcp_server.build_server()
    f = tmp_path / "n.md"
    f.write_text("# n")
    for tool, args in (("hippocampus_search", {"query": "q", "top_k": 500}), ("hippocampus_index_doc", {"path": str(f)})):
        assert "api is down" in str(asyncio.run(server.call_tool(tool, args)))


def test_main_runs_the_server(monkeypatch):
    ran = []
    monkeypatch.setattr(mcp_server, "build_server", lambda: type("S", (), {"run": lambda self: ran.append(1)})())
    mcp_server.main()
    assert ran == [1]


# --- jobs


def test_registry_evicts_the_oldest_finished_job():
    reg = JobRegistry(max_jobs=1)
    first = reg.submit("x", lambda job: {"n": 1}, background=False)
    second = reg.submit("x", lambda job: {"n": 2}, background=False)
    assert reg.get(first.id) is None and reg.get(second.id).status == "done"


def test_failed_foreground_job_records_the_error():
    def boom(job):
        raise ValueError("bad")

    job = JobRegistry().submit("x", boom, background=False)
    assert job.status == "error" and job.error == "ValueError: bad"


# --- chunker


def test_overlap_ratio_is_validated():
    with pytest.raises(ValueError, match="overlap_ratio"):
        chunker.chunk_markdown("# a\n\ntext", overlap_ratio=0.5)


def test_oversized_paragraph_is_split_on_words():
    text = "# H\n\n" + " ".join(f"word{i}" for i in range(300))
    chunks = chunker.chunk_markdown(text, target_tokens=40)
    assert len(chunks) > 3 and all(c.header_path == "H" for c in chunks)
