import hashlib
import hmac
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from hippocampus.api import create_app
from hippocampus.docs_sync import DocsSyncConfig, is_indexable, plan_push
from hippocampus.jobs import JobRegistry

from test_api import KeywordEmbedder, MemStore

SECRET = "hook-secret"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
INFRA = "private-infra/docs/runbook.md"


class FakeSource:
    def __init__(self, files):
        self.files = dict(files)
        self.fetched = []

    def list_files(self, ref):
        return list(self.files)

    def read_file(self, path, ref):
        self.fetched.append(path)
        return self.files.get(path)


class CountingEmbedder(KeywordEmbedder):
    def __init__(self):
        self.seen = []

    def embed(self, texts):
        self.seen.extend(texts)
        return super().embed(texts)


def setup(files):
    mem = MemStore()
    mem.create_collection("docs", "docs", None)
    source = FakeSource(files)
    cfg = DocsSyncConfig("docs", "acme-org", "docs-repo", "main", SECRET, source)
    embedder = CountingEmbedder()

    @contextmanager
    def factory():
        yield mem

    return TestClient(create_app(factory, embedder, JobRegistry(), docs_sync=cfg)), mem, source, embedder


def push(client, commits, ref="refs/heads/main", repo="acme-org/docs-repo", secret=SECRET, sig=True):
    body = json.dumps({"ref": ref, "after": "abc123", "repository": {"full_name": repo}, "commits": commits}).encode()
    headers = {"X-Forgejo-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()} if sig else {}
    return client.post("/v1/webhooks/forgejo", content=body, headers=headers)


def finished(client, resp):
    job = client.get(f"/v1/jobs/{resp.json()['job_id']}").json()
    assert job["status"] == "done", job
    return job["result"]


@pytest.mark.parametrize("path,ok", [
    ("team/plans/x.md", True), ("README.MD", True), ("a/b.txt", False), ("img/x.png", False),
    (INFRA, False), ("private-infra/docs/a.md", False), ("x/PRIVATE-INFRA/docs/a.md", False),
    ("../etc/passwd.md", False), ("/abs.md", False),
])
def test_is_indexable(path, ok):
    assert is_indexable(path) is ok


def test_incremental_reindexes_only_affected_files():
    files = {"a.md": "# A\n\ncats", "b.md": "# B\n\ndogs", "c.md": "# C\n\nbirds"}
    c, mem, source, emb = setup(files)
    assert finished(c, c.post("/v1/sync/docs"))["added"] == 3
    emb.seen.clear()
    source.fetched.clear()
    source.files["b.md"] = "# B\n\ndogs and more"
    r = push(c, [{"added": [], "modified": ["b.md"], "removed": []}])
    assert r.status_code == 202 and r.json()["mode"] == "incremental"
    assert finished(c, r)["changed"] == 1
    assert source.fetched == ["b.md"]
    assert all("dogs" in t for t in emb.seen)
    assert sorted(mem.collections["docs"]) == ["repo://docs-repo/a.md", "repo://docs-repo/b.md", "repo://docs-repo/c.md"]


def test_removed_file_leaves_the_collection():
    c, mem, source, _ = setup({"a.md": "# A\n\ncats", "b.md": "# B\n\ndogs"})
    finished(c, c.post("/v1/sync/docs"))
    res = finished(c, push(c, [{"added": [], "modified": [], "removed": ["a.md"]}]))
    assert res["removed"] == 1
    assert list(mem.collections["docs"]) == ["repo://docs-repo/b.md"]


def test_infrastructure_docs_are_never_fetched_or_embedded():
    c, mem, source, emb = setup({"a.md": "# A\n\ncats", INFRA: "# Runbook\n\n10.0.0.1 firewall"})
    r = push(c, [{"added": [INFRA, "a.md"], "modified": ["private-infra/docs/x.md"], "removed": []}])
    res = finished(c, r)
    assert res["skipped"] == 2
    assert source.fetched == ["a.md"]
    assert not any("firewall" in t for t in emb.seen)
    assert list(mem.collections["docs"]) == ["repo://docs-repo/a.md"]


def test_infrastructure_docs_are_never_ingested_by_full_resync():
    c, mem, source, emb = setup({"a.md": "# A\n\ncats", INFRA: "# Runbook\n\n10.0.0.1 firewall"})
    finished(c, c.post("/v1/sync/docs"))
    assert source.fetched == ["a.md"]
    assert not any("firewall" in t for t in emb.seen)
    assert list(mem.collections["docs"]) == ["repo://docs-repo/a.md"]


def test_resync_prunes_sources_that_left_the_branch():
    c, mem, source, _ = setup({"a.md": "# A\n\ncats", "b.md": "# B\n\ndogs"})
    finished(c, c.post("/v1/sync/docs"))
    del source.files["b.md"]
    assert finished(c, c.post("/v1/sync/docs"))["removed"] == 1
    assert list(mem.collections["docs"]) == ["repo://docs-repo/a.md"]


def test_file_with_a_secret_is_rejected_and_unindexed():
    c, mem, source, emb = setup({"a.md": "# A\n\ncats"})
    finished(c, c.post("/v1/sync/docs"))
    source.files["a.md"] = f"# A\n\nkey {FAKE_AWS}"
    res = finished(c, push(c, [{"added": [], "modified": ["a.md"], "removed": []}]))
    assert res["rejected"] == [{"path": "a.md", "reasons": ["aws-access-key-id"]}]
    assert mem.collections["docs"] == {}
    assert not any(FAKE_AWS in t for t in emb.seen)


def test_signature_is_required_and_checked():
    c, *_ = setup({})
    commits = [{"added": ["a.md"], "modified": [], "removed": []}]
    assert push(c, commits, sig=False).status_code == 401
    assert push(c, commits, secret="wrong").status_code == 401


def test_other_repo_or_branch_is_ignored():
    c, mem, source, _ = setup({"a.md": "# A"})
    commits = [{"added": ["a.md"], "modified": [], "removed": []}]
    assert "ignored" in push(c, commits, repo="acme-org/Other").json()
    assert "ignored" in push(c, commits, ref="refs/heads/feature").json()
    assert source.fetched == []


def test_large_push_falls_back_to_full_resync():
    c, mem, source, _ = setup({"a.md": "# A\n\ncats"})
    r = push(c, [{"added": [], "modified": [], "removed": []}] * 20)
    assert r.json()["mode"] == "full"
    assert finished(c, r)["added"] == 1


def test_last_action_on_a_path_wins():
    cfg = DocsSyncConfig("c", "o", "r", "main", "s", FakeSource({}))
    plan = plan_push(cfg, {"ref": "refs/heads/main", "repository": {"full_name": "o/r"}, "commits": [
        {"added": ["x.md"], "modified": [], "removed": []}, {"added": [], "modified": [], "removed": ["x.md"]},
        {"added": [], "modified": [], "removed": ["y.md"]}, {"added": ["y.md"], "modified": [], "removed": []}]})
    assert plan.upserts == {"y.md"} and plan.removes == {"x.md"}


def test_not_configured_is_501():
    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    c = TestClient(create_app(factory, KeywordEmbedder(), JobRegistry()))
    assert c.post("/v1/sync/docs").status_code == 501
    assert c.post("/v1/webhooks/forgejo", content=b"{}").status_code == 501
