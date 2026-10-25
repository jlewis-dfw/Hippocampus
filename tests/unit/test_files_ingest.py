"""POST /v1/ingest/files: batched folder uploads (F2). Paths, secrets and sizes are checked again on the server."""
from test_access import add_client, build

TOKEN = "ghp_" + "a" * 36


def files(**docs):
    return [{"path": p, "content": t} for p, t in docs.items()]


def setup(**kw):
    c, mem = build(**kw)
    c.post("/v1/collections", json={"slug": "notes", "name": "Notes"})
    return c, mem


def uris(mem):
    return sorted(mem.collections["notes"])


def test_single_batch_final_indexes_and_reports_each_file():
    c, mem = setup()
    r = c.post("/v1/ingest/files", json={
        "collection": "notes", "source": "vault", "final": True,
        "files": files(**{"Work/a.md": "# A\ncat", "Work\\b.md": "# B\ndog", ".obsidian/x.md": "x", "c.md": f"t {TOKEN}"})})
    assert r.status_code == 200
    body = r.json()
    assert body["session"] is None and body["totals"]["added"] == 2
    by = {f["path"]: f for f in body["files"]}
    assert by["Work/a.md"]["action"] == "added" and by["Work/b.md"]["outcome"] == "indexed"
    assert by[".obsidian/x.md"]["reason"] == "default-exclude"
    assert by["c.md"] == {"path": "c.md", "outcome": "rejected", "reason": "github-token"}
    assert TOKEN not in r.text
    assert uris(mem) == ["files://vault/Work/a.md", "files://vault/Work/b.md"]


def test_many_batches_one_run_prunes_only_on_the_final_batch():
    c, mem = setup()
    c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True,
                                     "files": files(**{"old.md": "# Old", "keep.md": "# Keep"})})
    first = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault",
                                             "files": files(**{"keep.md": "# Keep", "new1.md": "# N1"})}).json()
    assert first["session"] and first["removed"] == []
    assert "files://vault/old.md" in uris(mem)  # nothing is pruned mid-run
    last = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True,
                                            "session": first["session"], "files": files(**{"new2.md": "# N2"})}).json()
    assert last["removed"] == ["old.md"] and last["session"] is None
    assert last["totals"] | {"files": 0} == {"added": 2, "changed": 0, "unchanged": 1, "removed": 1, "skipped": 0,
                                             "rejected": 0, "files": 0}
    assert uris(mem) == ["files://vault/keep.md", "files://vault/new1.md", "files://vault/new2.md"]
    # the session is closed
    again = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "session": first["session"],
                                             "files": []})
    assert again.status_code == 404


def test_second_run_only_changes_what_changed_and_crlf_does_not_count():
    c, mem = setup()
    run = lambda **d: c.post("/v1/ingest/files", json={  # noqa: E731
        "collection": "notes", "source": "vault", "final": True, "files": files(**d)}).json()
    run(**{"a.md": "# A\nline\n", "b.md": "# B", "gone.md": "# G"})
    second = run(**{"a.md": "# A\r\nline\r\n", "b.md": "# B changed"})
    assert {f["path"]: f["action"] for f in second["files"]} == {"a.md": "unchanged", "b.md": "changed"}
    assert second["removed"] == ["gone.md"]


def test_a_file_that_gains_a_secret_is_removed_and_reported():
    c, mem = setup()
    c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True,
                                     "files": files(**{"a.md": "# A\nfine"})})
    assert uris(mem) == ["files://vault/a.md"]
    # not even a final batch: the removal is immediate
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault",
                                         "files": files(**{"a.md": f"# A\ntoken {TOKEN}"})}).json()
    assert r["files"][0] == {"path": "a.md", "outcome": "rejected", "reason": "github-token", "action": "removed"}
    assert uris(mem) == [] and r["totals"]["removed"] == 1


def test_unsafe_paths_are_rejected_not_stored():
    c, mem = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True, "files": files(
        **{"../evil.md": "x", "C:\\win.md": "x", "/abs.md": "x", "NUL.md": "x", ".env": "A=1", "ok.md": "# ok"})}).json()
    assert {f["path"]: f["reason"] for f in r["files"] if f["outcome"] == "rejected"} == {
        "../evil.md": "invalid-path:path-traversal", "C:\\win.md": "invalid-path:drive-letter",
        "/abs.md": "invalid-path:absolute-path", "NUL.md": "invalid-path:windows-reserved-name",
        ".env": "secret-file-name"}
    assert uris(mem) == ["files://vault/ok.md"]


def test_server_applies_the_private_infra_guard_whatever_the_client_says():
    c, mem = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True, "files": files(
        **{"private-infra/a.md": "# ips"}), "filter": {"default_excludes": False, "exclude": []}}).json()
    assert r["files"][0]["reason"] == "excluded-source" and uris(mem) == []


def test_filter_rules_and_extensions():
    c, mem = setup()
    r = c.post("/v1/ingest/files", json={
        "collection": "notes", "source": "vault", "final": True,
        "filter": {"include": ["Work/"], "exclude": ["Work/Clients/"], "extensions": ["md", "py"]},
        "files": files(**{"Work/a.md": "# A", "Work/s.py": "print(1)", "Work/Clients/x.md": "# X", "Journal/j.md": "# J",
                          "Work/p.png": "x"})}).json()
    assert {f["path"]: f.get("reason") or f["action"] for f in r["files"]} == {
        "Work/a.md": "added", "Work/s.py": "added", "Work/Clients/x.md": "excluded-by-rule",
        "Journal/j.md": "not-included", "Work/p.png": "extension"}


def test_changing_the_rules_and_syncing_again_removes_files_no_longer_included():
    c, mem = setup()
    both = files(**{"Work/a.md": "# A", "Clients/x.md": "# X"})
    c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True, "files": both})
    assert len(uris(mem)) == 2
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True, "files": both,
                                         "filter": {"exclude": ["Clients/"]}}).json()
    assert r["removed"] == ["Clients/x.md"] and uris(mem) == ["files://vault/Work/a.md"]


def test_sources_do_not_touch_each_other():
    c, mem = setup()
    for src in ("one", "two"):
        c.post("/v1/ingest/files", json={"collection": "notes", "source": src, "final": True,
                                         "files": files(**{"a.md": "# A"})})
    c.post("/v1/ingest/files", json={"collection": "notes", "source": "one", "final": True, "files": []})
    assert uris(mem) == ["files://two/a.md"]


def test_dry_run_writes_nothing_and_accepts_paths_without_content():
    c, mem = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "dry_run": True, "files": [
        {"path": "a.md", "size": 10}, {"path": "big.md", "size": 500_000}, {"path": "img.png", "size": 5},
        {"path": "s.md", "content": f"t {TOKEN}"}]}).json()
    assert r["dry_run"] and r["counts"] == {"indexed": 1, "skipped": 1, "rejected": 2}
    assert uris(mem) == [] and "session" not in r


def test_content_is_required_outside_a_dry_run():
    c, _ = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "files": [{"path": "a.md"}]})
    assert r.status_code == 422


def test_request_validation():
    c, _ = setup()
    base = {"collection": "notes", "source": "vault", "files": []}
    assert c.post("/v1/ingest/files", json=base | {"source": "../x"}).status_code == 422
    assert c.post("/v1/ingest/files", json=base | {"collection": "Bad Slug"}).status_code == 422
    assert c.post("/v1/ingest/files", json=base | {"files": files(**{f"{i}.md": "x" for i in range(501)})}).status_code == 422
    assert c.post("/v1/ingest/files", json=base | {"filter": {"include": ["x" * 301]}}).status_code == 422
    assert c.post("/v1/ingest/files", json=base | {"collection": "nope"}).status_code == 404


def test_oversized_batch_is_refused(monkeypatch):
    import hippocampus.routes.uploads as api
    monkeypatch.setattr(api, "MAX_BATCH_CHARS", 10)
    c, _ = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "files": files(**{"a.md": "x" * 11})})
    assert r.status_code == 413


def test_run_size_limit(monkeypatch):
    import hippocampus.files_ingest as fi
    monkeypatch.setattr(fi, "MAX_SESSION_FILES", 2)
    c, _ = setup()
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault",
                                         "files": files(**{"a.md": "# a", "b.md": "# b", "c.md": "# c"})})
    assert r.status_code == 413


def test_too_many_runs_in_progress(monkeypatch):
    import hippocampus.files_ingest as fi
    monkeypatch.setattr(fi, "MAX_SESSIONS", 1)
    c, _ = setup()
    body = {"collection": "notes", "source": "vault", "files": []}
    assert c.post("/v1/ingest/files", json=body).status_code == 200
    assert c.post("/v1/ingest/files", json=body).status_code == 429


def test_a_thousand_files_in_ten_batches_then_a_second_run_changes_one_and_removes_one():
    c, mem = setup()
    docs = {f"d{i // 100}/n{i}.md": f"# N{i}\nbody {i}" for i in range(1000)}
    items = list(docs.items())
    sid = None
    for n in range(10):
        chunk = items[n * 100:(n + 1) * 100]
        r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "big", "session": sid, "final": n == 9,
                                             "files": [{"path": p, "content": t} for p, t in chunk]}).json()
        sid = r["session"]
    assert r["totals"]["added"] == 1000 and len(mem.collections["notes"]) == 1000
    docs["d0/n0.md"] = "# changed"
    del docs["d9/n999.md"]
    items = list(docs.items())
    sid = None
    for n, part in enumerate((items[:500], items[500:])):
        r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "big", "session": sid, "final": n == 1,
                                             "files": [{"path": p, "content": t} for p, t in part]}).json()
        sid = r["session"]
    assert r["totals"] | {"files": 0} == {"added": 0, "changed": 1, "unchanged": 998, "removed": 1, "skipped": 0,
                                          "rejected": 0, "files": 0}
    assert r["removed"] == ["d9/n999.md"]


# --- access control: denied (403) and hidden (404) ---

def test_anonymous_cannot_write_when_auth_is_required():
    c, mem = build(require_auth=True)
    admin = add_client(mem, "root", "hip_root", admin=True)
    c.post("/v1/collections", json={"slug": "notes", "name": "N"}, headers=admin)
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "files": files(**{"a.md": "# A"})})
    assert r.status_code == 403 and uris(mem) == []


def test_read_only_account_is_denied_and_hidden_collection_is_404():
    c, mem = build(require_auth=True)
    admin = add_client(mem, "root", "hip_root", admin=True)
    reader = add_client(mem, "reader", "hip_reader", read=["notes"])
    writer = add_client(mem, "writer", "hip_writer", write=["notes"])
    stranger = add_client(mem, "stranger", "hip_stranger", write=["other"])
    c.post("/v1/collections", json={"slug": "notes", "name": "N", "visibility": "restricted"}, headers=admin)
    body = {"collection": "notes", "source": "vault", "final": True, "files": files(**{"a.md": "# A"})}

    assert c.post("/v1/ingest/files", json=body, headers=reader).status_code == 403  # can see it, cannot write
    assert c.post("/v1/ingest/files", json=body, headers=stranger).status_code == 404  # hidden: looks missing
    assert c.post("/v1/ingest/files", json=body).status_code == 404  # anonymous: hidden too
    assert c.post("/v1/ingest/files", json=body | {"dry_run": True}, headers=stranger).status_code == 404
    assert uris(mem) == []
    assert c.post("/v1/ingest/files", json=body, headers=writer).status_code == 200
    assert uris(mem) == ["files://vault/a.md"]


def test_a_session_belongs_to_the_account_that_started_it():
    c, mem = build(require_auth=True)
    admin = add_client(mem, "root", "hip_root", admin=True)
    a = add_client(mem, "a", "hip_a", write=["notes"])
    b = add_client(mem, "b", "hip_b", write=["notes"])
    c.post("/v1/collections", json={"slug": "notes", "name": "N"}, headers=admin)
    sid = c.post("/v1/ingest/files", json={"collection": "notes", "source": "v", "files": files(**{"a.md": "# A"})},
                 headers=a).json()["session"]
    stolen = c.post("/v1/ingest/files", json={"collection": "notes", "source": "v", "session": sid, "final": True,
                                              "files": []}, headers=b)
    assert stolen.status_code == 404
    assert "files://v/a.md" in mem.collections["notes"]  # b could not prune a's run
