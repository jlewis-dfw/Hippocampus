"""Saved sync sources (F4) and how their runs are recorded (F5)."""
import pytest
from test_access import add_client, build

TOKEN = "ghp_" + "a" * 36


def files(**docs):
    return [{"path": p, "content": t} for p, t in docs.items()]


def run(c, collection="notes", source="vault", final=True, headers=None, **kw):
    return c.post("/v1/ingest/files", json={"collection": collection, "source": source, "final": final, **kw},
                  headers=headers or {})


def make():
    c, mem = build()
    for slug in ("notes", "notes-work"):
        c.post("/v1/collections", json={"slug": slug, "name": slug})
    return c, mem


def test_source_lifecycle():
    c, mem = make()
    body = {"name": "vault", "collection": "notes", "settings": {"exclude": ["Archive/"], "folders": ["Work"]}}
    r = c.post("/v1/sources", json=body)
    assert r.status_code == 201
    src = r.json()
    assert src["kind"] == "upload" and src["settings"]["folders"] == ["Work"] and src["last_status"] is None
    assert c.post("/v1/sources", json=body).status_code == 409
    assert [s["name"] for s in c.get("/v1/sources").json()["sources"]] == ["vault"]
    assert c.get("/v1/sources/vault").json()["collection"] == "notes"
    r = c.patch("/v1/sources/vault", json={"settings": {"folders": ["Work", "Projects"]}})
    assert r.json()["settings"]["folders"] == ["Work", "Projects"] and r.json()["collection"] == "notes"
    assert c.delete("/v1/sources/vault").status_code == 204
    assert c.get("/v1/sources/vault").status_code == 404
    assert c.delete("/v1/sources/vault").status_code == 404


def test_source_validation():
    c, _ = make()
    ok = {"name": "vault", "collection": "notes"}
    assert c.post("/v1/sources", json=ok | {"name": "../x"}).status_code == 422
    assert c.post("/v1/sources", json=ok | {"kind": "s3"}).status_code == 422  # no reader for it yet
    assert c.post("/v1/sources", json=ok | {"collection": "Bad Slug"}).status_code == 422
    assert c.post("/v1/sources", json=ok | {"collection": "nope"}).status_code == 404
    assert c.post("/v1/sources", json=ok | {"settings": {"include": ["x" * 301]}}).status_code == 422
    assert c.post("/v1/sources", json=ok | {"settings": {"folders": ["a/b"]}}).status_code == 422
    assert c.post("/v1/sources", json=ok | {"folder_collections": {"a/b": "notes"}}).status_code == 422
    assert c.post("/v1/sources", json=ok | {"folder_collections": {"Work": "nope"}}).status_code == 404


def test_the_saved_rules_win_over_the_request_and_unticking_a_folder_removes_its_files():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes", "settings": {"folders": ["Work", "Projects"]}})
    docs = files(**{"Work/a.md": "# A", "Projects/b.md": "# B", "Journal/j.md": "# J"})
    r = run(c, files=docs, filter={"folders": ["Journal"]}).json()  # the request's own filter is ignored
    assert sorted(mem.collections["notes"]) == ["files://vault/Projects/b.md", "files://vault/Work/a.md"]
    assert {f["path"]: f.get("reason") for f in r["files"]}["Journal/j.md"] == "not-included"
    c.patch("/v1/sources/vault", json={"settings": {"folders": ["Work"]}})
    r = run(c, files=docs).json()
    assert r["removed"] == ["Projects/b.md"] and sorted(mem.collections["notes"]) == ["files://vault/Work/a.md"]


def test_a_run_is_recorded_on_the_source_and_as_a_job():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"})
    r = run(c, files=files(**{"a.md": "# A", "b.md": "# B", "c.md": f"t {TOKEN}"})).json()
    src = c.get("/v1/sources/vault").json()
    assert src["last_status"] == "ok" and src["last_run_at"]
    assert src["last_summary"] == {"collection": "notes", "added": 2, "changed": 0, "unchanged": 0, "removed": 0,
                                   "skipped": 0, "rejected": 1, "files": 3}
    job = c.get(f"/v1/jobs/{r['job_id']}").json()
    assert job["kind"] == "sync-files" and job["status"] == "done" and job["result"]["added"] == 2
    assert TOKEN not in str(src) + str(job)


def test_a_failed_run_leaves_the_index_and_shows_why():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"})
    run(c, files=files(**{"a.md": "# A"}))
    before = dict(mem.collections["notes"])

    class Boom(Exception):
        pass

    original = mem.replace_source

    def fail(*a, **k):
        raise Boom("disk full " + TOKEN)

    mem.replace_source = fail
    try:
        with pytest.raises(Boom):
            run(c, files=files(**{"a.md": "# A changed", "new.md": "# New"}))
    finally:
        mem.replace_source = original
    assert mem.collections["notes"] == before  # nothing pruned, nothing lost
    src = c.get("/v1/sources/vault").json()
    assert src["last_status"] == "error" and src["last_summary"] == {"collection": "notes", "error": "Boom"}
    assert TOKEN not in str(src)  # the cause is named by type, never by message


def test_abort_cancels_a_run_without_pruning_and_records_it():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"})
    run(c, files=files(**{"a.md": "# A", "b.md": "# B"}))
    sid = run(c, final=False, files=files(**{"a.md": "# A"})).json()["session"]
    assert c.delete(f"/v1/ingest/files/{sid}?reason=cancelled+by+user").status_code == 204
    assert sorted(mem.collections["notes"]) == ["files://vault/a.md", "files://vault/b.md"]
    assert c.get("/v1/sources/vault").json()["last_summary"] == {"collection": "notes", "error": "cancelled by user"}
    assert c.delete(f"/v1/ingest/files/{sid}").status_code == 404  # gone
    assert run(c, final=False, session=sid, files=[]).status_code == 404


def test_per_folder_collections_keep_each_folder_in_its_own_collection():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes", "folder_collections": {"Work": "notes-work"}})
    docs = files(**{"Work/a.md": "# A", "Projects/b.md": "# B", "top.md": "# T"})
    run(c, collection="notes", files=docs)
    run(c, collection="notes-work", files=docs)
    assert sorted(mem.collections["notes"]) == ["files://vault/Projects/b.md", "files://vault/top.md"]
    assert sorted(mem.collections["notes-work"]) == ["files://vault/Work/a.md"]
    # a collection the source does not use cannot borrow its name
    c.post("/v1/collections", json={"slug": "elsewhere", "name": "e"})
    assert run(c, collection="elsewhere", files=docs).status_code == 409


def test_an_empty_final_batch_releases_a_collection_the_source_no_longer_uses():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes", "folder_collections": {"Work": "notes-work"}})
    docs = files(**{"Work/a.md": "# A", "top.md": "# T"})
    run(c, collection="notes", files=docs)
    run(c, collection="notes-work", files=docs)
    c.patch("/v1/sources/vault", json={"folder_collections": {}})  # Work now goes to the base collection
    assert run(c, collection="notes-work", files=docs).status_code == 409  # a real run may not borrow the name
    r = run(c, collection="notes-work", files=[])  # but an empty final batch may clear what the source left there
    assert r.status_code == 200 and r.json()["removed"] == ["Work/a.md"]
    assert mem.collections["notes-work"] == {}
    assert sorted(mem.collections["notes"]) == ["files://vault/top.md"]  # other collections are untouched


def test_a_mapped_collection_with_its_folder_unticked_gets_nothing():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes",
                                "settings": {"folders": ["Projects"]}, "folder_collections": {"Work": "notes-work"}})
    run(c, collection="notes-work", files=files(**{"Work/a.md": "# A", "Projects/b.md": "# B"}))
    assert mem.collections["notes-work"] == {}


def test_sync_now_says_to_drop_the_folder_again_for_an_upload_source():
    c, _ = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"})
    r = c.post("/v1/sources/vault/sync")
    assert r.status_code == 409 and "drop the folder again" in r.json()["detail"]
    assert c.post("/v1/sources/missing/sync").status_code == 404


def test_delete_with_purge_removes_the_indexed_files_and_without_it_they_stay():
    c, mem = make()
    for name in ("vault", "other"):
        c.post("/v1/sources", json={"name": name, "collection": "notes", "folder_collections": {"Work": "notes-work"}})
        run(c, source=name, files=files(**{"a.md": "# A"}))
        run(c, collection="notes-work", source=name, files=files(**{"Work/w.md": "# W"}))
    assert c.delete("/v1/sources/other").status_code == 204
    assert len(mem.collections["notes"]) == 2  # files stay searchable
    assert c.delete("/v1/sources/vault?purge=true").status_code == 204
    assert sorted(mem.collections["notes"]) == ["files://other/a.md"]
    assert sorted(mem.collections["notes-work"]) == ["files://other/Work/w.md"]


def test_deleting_the_collection_removes_its_sources():
    c, mem = make()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"})
    assert c.delete("/v1/collections/notes").status_code == 204
    assert c.get("/v1/sources").json() == {"sources": []}


# --- access control: denied (403) and hidden (404) ---

def world():
    c, mem = build(require_auth=True)
    admin = add_client(mem, "root", "hip_root", admin=True)
    reader = add_client(mem, "reader", "hip_reader", read=["notes"])
    writer = add_client(mem, "writer", "hip_writer", write=["notes", "notes-work"])
    narrow = add_client(mem, "narrow", "hip_narrow", write=["notes"])
    stranger = add_client(mem, "stranger", "hip_stranger", write=["other"])
    c.post("/v1/collections", json={"slug": "notes", "name": "N", "visibility": "restricted"}, headers=admin)
    c.post("/v1/collections", json={"slug": "notes-work", "name": "W", "visibility": "restricted"}, headers=admin)
    c.post("/v1/collections", json={"slug": "other", "name": "O", "visibility": "restricted"}, headers=admin)
    return c, mem, dict(admin=admin, reader=reader, writer=writer, narrow=narrow, stranger=stranger)


def test_creating_a_source_needs_write_and_a_hidden_collection_looks_missing():
    c, mem, h = world()
    body = {"name": "vault", "collection": "notes"}
    assert c.post("/v1/sources", json=body, headers=h["reader"]).status_code == 403
    assert c.post("/v1/sources", json=body, headers=h["stranger"]).status_code == 404
    assert c.post("/v1/sources", json=body).status_code == 404
    assert mem.sync_sources == {}
    assert c.post("/v1/sources", json=body, headers=h["writer"]).status_code == 201


def test_every_folder_collection_needs_write_too():
    c, mem, h = world()
    body = {"name": "vault", "collection": "notes", "folder_collections": {"Work": "notes-work"}}
    assert c.post("/v1/sources", json=body, headers=h["narrow"]).status_code == 404  # cannot see notes-work
    assert mem.sync_sources == {}


def test_sources_are_hidden_from_accounts_that_cannot_read_the_collection():
    c, mem, h = world()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"}, headers=h["writer"])
    assert [s["name"] for s in c.get("/v1/sources", headers=h["reader"]).json()["sources"]] == ["vault"]
    assert c.get("/v1/sources", headers=h["stranger"]).json() == {"sources": []}
    assert c.get("/v1/sources").json() == {"sources": []}
    assert c.get("/v1/sources/vault", headers=h["stranger"]).status_code == 404
    assert c.get("/v1/sources/vault", headers=h["reader"]).status_code == 200


def test_edit_run_and_delete_are_denied_to_readers_and_hidden_from_strangers():
    c, mem, h = world()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes"}, headers=h["writer"])
    for method, url, kw in (("patch", "/v1/sources/vault", {"json": {"settings": {"folders": ["X"]}}}),
                            ("post", "/v1/sources/vault/sync", {}), ("delete", "/v1/sources/vault", {})):
        assert getattr(c, method)(url, headers=h["reader"], **kw).status_code == 403, url
        assert getattr(c, method)(url, headers=h["stranger"], **kw).status_code == 404, url
        assert getattr(c, method)(url, **kw).status_code == 404, url
    assert c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True,
                                            "files": files(**{"a.md": "# A"})}, headers=h["reader"]).status_code == 403
    assert mem.sync_sources["vault"]["settings"]["folders"] == [] and mem.sync_sources["vault"]["last_status"] is None
    assert c.patch("/v1/sources/vault", json={"collection": "other"}, headers=h["writer"]).status_code == 404


def test_purge_needs_write_on_every_collection_it_touches():
    c, mem, h = world()
    c.post("/v1/sources", json={"name": "vault", "collection": "notes", "folder_collections": {"Work": "notes-work"}},
           headers=h["writer"])
    mem.collections["notes-work"]["files://vault/Work/w.md"] = ("W", "h", [])
    add_client(mem, "half", "hip_half", write=["notes"])
    # "half" cannot see notes-work, so it cannot purge there
    assert c.delete("/v1/sources/vault?purge=true", headers={"Authorization": "Bearer hip_half"}).status_code == 404
    assert "vault" in mem.sync_sources and "files://vault/Work/w.md" in mem.collections["notes-work"]


def test_another_account_cannot_abort_a_run():
    c, mem, h = world()
    sid = c.post("/v1/ingest/files", json={"collection": "notes", "source": "v", "files": files(**{"a.md": "# A"})},
                 headers=h["writer"]).json()["session"]
    assert c.delete(f"/v1/ingest/files/{sid}", headers=h["narrow"]).status_code == 404
    assert c.delete(f"/v1/ingest/files/{sid}", headers=h["writer"]).status_code == 204
