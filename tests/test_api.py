import math
from contextlib import contextmanager

from fastapi.testclient import TestClient

from hippocampus.api import create_app
from hippocampus.jobs import JobRegistry


class KeywordEmbedder:
    """Deterministic 384-dim vectors: dimension 0 = 'cat', 1 = 'dog', 2 = everything else."""

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 384
            low = t.lower()
            v[0] = float("cat" in low)
            v[1] = float("dog" in low)
            v[2] = 0.1
            out.append(v)
        return out


class MemStore:
    def __init__(self):
        self.collections = {}  # slug -> {uri: (title, sha, chunks)}
        self.visibility = {}  # slug -> shared | restricted
        self.clients = {}  # name -> dict(token_sha256, is_admin, read_grants, write_grants, note, revoked)

    def collection_exists(self, slug):
        return slug in self.collections

    def create_collection(self, slug, name, description, visibility="shared"):
        if slug in self.collections:
            return False
        self.collections[slug] = {}
        self.visibility[slug] = visibility
        return True

    def collection_visibility(self, slug):
        return self.visibility.get(slug)

    def update_collection(self, slug, name, description, visibility):
        if slug not in self.collections:
            return False
        if visibility:
            self.visibility[slug] = visibility
        return True

    def find_client(self, token_sha256):
        for name, c in self.clients.items():
            if c["token_sha256"] == token_sha256 and not c["revoked"]:
                return {"name": name, "is_admin": c["is_admin"], "read_grants": c["read_grants"],
                        "write_grants": c["write_grants"]}
        return None

    def list_clients(self):
        return [{"name": n, "admin": c["is_admin"], "read": c["read_grants"], "write": c["write_grants"],
                 "note": c["note"], "created_at": "x", "last_used_at": None, "revoked": c["revoked"]}
                for n, c in sorted(self.clients.items())]

    def create_client(self, name, token_sha256, admin, read, write, note):
        if name in self.clients:
            return False
        self.clients[name] = {"token_sha256": token_sha256, "is_admin": admin, "read_grants": read,
                              "write_grants": write, "note": note, "revoked": False}
        return True

    def update_client(self, name, admin, read, write, note):
        c = self.clients.get(name)
        if c is None or c["revoked"]:
            return False
        for key, val in (("is_admin", admin), ("read_grants", read), ("write_grants", write), ("note", note)):
            if val is not None:
                c[key] = val
        return True

    def rotate_client(self, name, token_sha256):
        c = self.clients.get(name)
        if c is None or c["revoked"]:
            return False
        c["token_sha256"] = token_sha256
        return True

    def revoke_client(self, name):
        c = self.clients.get(name)
        if c is None or c["revoked"]:
            return False
        c["revoked"] = True
        return True

    def count_admins(self):
        return sum(1 for c in self.clients.values() if c["is_admin"] and not c["revoked"])

    def delete_collection(self, slug):
        return self.collections.pop(slug, None) is not None

    def list_collections(self):
        return [
            {"slug": s, "name": s, "description": None, "created_at": "x", "visibility": self.visibility[s],
             "source_count": len(d), "chunk_count": sum(len(v[2]) for v in d.values())}
            for s, d in sorted(self.collections.items())
        ]

    def list_sources(self, slug, limit=200):
        if slug not in self.collections:
            return None
        return [{"uri": u, "title": v[0], "status": "indexed", "chunk_count": len(v[2]), "sha256": v[1][:12],
                 "error": None, "last_indexed_at": "2026-01-01T00:00:00+00:00"}
                for u, v in list(self.collections[slug].items())[:limit]]

    def source_hashes(self, collection):
        return {u: v[1] for u, v in self.collections[collection].items()}

    def replace_source(self, collection, uri, title, sha256, chunks):
        self.collections[collection][uri] = (title, sha256, chunks)

    def delete_source(self, collection, uri):
        self.collections[collection].pop(uri, None)

    def search(self, embedding, collections, top_k, min_score):
        def cos(a, b):
            d = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b, strict=True)) / d

        hits = []
        for slug, docs in self.collections.items():
            if slug not in collections:  # like PgStore: an empty list searches nothing
                continue
            for uri, (title, _, chunks) in docs.items():
                for ec in chunks:
                    hits.append({"collection": slug, "uri": uri, "title": title,
                                 "header_path": ec.chunk.header_path, "content": ec.chunk.content,
                                 "chunk_index": ec.chunk.index, "score": cos(embedding, ec.embedding)})
        hits.sort(key=lambda h: -h["score"])
        return [h for h in hits[:top_k] if h["score"] >= min_score]

    def totals(self, collections):
        return {"collections": len([s for s in self.collections if s in collections]), "sources": 0, "chunks": 0}


def make_client(crawler=None):
    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    jobs = JobRegistry()
    return TestClient(create_app(factory, KeywordEmbedder(), jobs, crawler)), mem


def test_collection_crud():
    c, _ = make_client()
    assert c.post("/v1/collections", json={"slug": "docs", "name": "Docs"}).status_code == 201
    assert c.post("/v1/collections", json={"slug": "docs", "name": "Docs"}).status_code == 409
    assert c.post("/v1/collections", json={"slug": "Bad Slug", "name": "x"}).status_code == 422
    assert [x["slug"] for x in c.get("/v1/collections").json()["collections"]] == ["docs"]
    assert c.delete("/v1/collections/docs").status_code == 204
    assert c.delete("/v1/collections/docs").status_code == 404


def test_ingest_and_search_ranking():
    c, _ = make_client()
    c.post("/v1/collections", json={"slug": "pets", "name": "Pets"})
    c.post("/v1/collections", json={"slug": "other", "name": "Other"})
    r = c.post("/v1/ingest/file", json={"collection": "pets", "uri": "cats.md", "text": "# Cats\nA cat sleeps."})
    assert r.json()["added"] == ["cats.md"]
    c.post("/v1/ingest/file", json={"collection": "pets", "uri": "dogs.md", "text": "# Dogs\nA dog barks."})
    c.post("/v1/ingest/file", json={"collection": "other", "uri": "n.md", "text": "# N\ncat nip"})

    res = c.post("/v1/search", json={"query": "cat", "collections": ["pets"], "top_k": 5}).json()["results"]
    assert res[0]["uri"] == "cats.md" and res[0]["header_path"]
    assert {h["collection"] for h in res} == {"pets"}
    assert res[0]["score"] > res[-1]["score"]

    assert all(h["score"] >= 0.9 for h in c.post("/v1/search", json={"query": "cat", "min_score": 0.9}).json()["results"])
    assert len(c.post("/v1/search", json={"query": "cat", "top_k": 1}).json()["results"]) == 1

    again = c.post("/v1/ingest/file", json={"collection": "pets", "uri": "cats.md", "text": "# Cats\nA cat sleeps."})
    assert again.json()["unchanged"] == ["cats.md"]


def test_ingest_guards():
    c, _ = make_client()
    assert c.post("/v1/ingest/file", json={"collection": "nope", "uri": "a.md", "text": "x"}).status_code == 404
    c.post("/v1/collections", json={"slug": "d", "name": "d"})
    r = c.post("/v1/ingest/file", json={"collection": "d", "uri": "file:///x/private-infra/docs/a.md", "text": "x"})
    assert r.status_code == 403
    assert c.post("/v1/search", json={"query": ""}).status_code == 422


def test_crawl_501_without_crawler():
    c, _ = make_client()
    c.post("/v1/collections", json={"slug": "d", "name": "d"})
    assert c.post("/v1/crawl", json={"url": "https://x.test", "collection": "d"}).status_code == 501


def test_crawl_job_status_and_sse():
    def crawler(job, req):
        job.log(f"fetching {req.url}")
        job.log("done")
        return {"pages": 1}

    c, _ = make_client(crawler)
    c.post("/v1/collections", json={"slug": "d", "name": "d"})
    r = c.post("/v1/crawl", json={"url": "https://x.test", "collection": "d", "depth": 2})
    assert r.status_code == 202
    jid = r.json()["job_id"]
    body = c.get(f"/v1/jobs/{jid}/events").text  # streams until the job ends
    assert "data: fetching https://x.test" in body and "event: end" in body
    job = c.get(f"/v1/jobs/{jid}").json()
    assert job["status"] == "done" and job["result"] == {"pages": 1}
    assert c.get("/v1/jobs/unknown").status_code == 404


def test_failed_job_reports_error():
    def crawler(job, req):
        raise RuntimeError("boom")

    c, _ = make_client(crawler)
    c.post("/v1/collections", json={"slug": "d", "name": "d"})
    jid = c.post("/v1/crawl", json={"url": "https://x.test", "collection": "d"}).json()["job_id"]
    c.get(f"/v1/jobs/{jid}/events")
    job = c.get(f"/v1/jobs/{jid}").json()
    assert job["status"] == "error" and "boom" in job["error"]


def test_status():
    c, _ = make_client()
    s = c.get("/v1/status").json()
    assert s["embedder"]["dimensions"] == 384 and "db_latency_ms" in s and s["active_jobs"] == 0


def test_network_gate():
    import ipaddress

    from hippocampus.api import parse_networks

    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    def client_from(ip, nets):
        app = create_app(factory, KeywordEmbedder(), allowed_networks=parse_networks(nets))
        return TestClient(app, client=(ip, 50000))

    allowed = "10.0.0.0/8,100.64.0.0/10"
    assert client_from("10.0.0.5", allowed).get("/v1/status").status_code == 200
    assert client_from("100.100.1.1", allowed).get("/v1/status").status_code == 200
    r = client_from("192.168.4.9", allowed).get("/v1/status")
    assert r.status_code == 403 and "not allowed" in r.json()["detail"]
    assert client_from("203.0.113.7", allowed).post("/v1/search", json={"query": "x"}).status_code == 403
    assert ipaddress.ip_network("10.0.0.0/8")  # parse sanity


def test_list_sources():
    c, _ = make_client()
    assert c.get("/v1/collections/nope/sources").status_code == 404
    assert c.get("/v1/collections/Bad Slug/sources").status_code == 422
    c.post("/v1/collections", json={"slug": "pets", "name": "Pets"})
    assert c.get("/v1/collections/pets/sources").json()["sources"] == []
    c.post("/v1/ingest/file", json={"collection": "pets", "uri": "cats.md", "title": "Cats", "text": "# Cats\nA cat."})
    src = c.get("/v1/collections/pets/sources").json()["sources"]
    assert [s["uri"] for s in src] == ["cats.md"] and src[0]["chunk_count"] >= 1
    assert c.get("/v1/collections/pets/sources?limit=0").status_code == 422


def test_ui_is_served_with_strict_headers():
    c, _ = make_client()
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"
    page = c.get("/ui/")
    assert page.status_code == 200 and "<title>Hippocampus</title>" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-cache"
    assert c.get("/ui/app.js").status_code == 200 and c.get("/ui/app.css").status_code == 200
    # the dashboard must not pull anything from outside its own origin
    for name in ("index.html", "app.js", "app.css"):
        assert "http://" not in c.get(f"/ui/{name}").text.replace("http://www.w3.org", "")
        assert "https://" not in c.get(f"/ui/{name}").text.replace("https://docs.example.com", "")


def test_network_gate_covers_api_but_not_ui():
    from hippocampus.api import parse_networks
    gated = TestClient(create_app(lambda: None, KeywordEmbedder(), allowed_networks=parse_networks("203.0.113.0/24")))
    assert gated.get("/v1/status").status_code == 403
    assert gated.get("/ui/").status_code == 200
