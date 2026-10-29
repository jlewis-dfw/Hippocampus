"""Saved web sources: the crawl settings behind a collection, re-crawl, and pruning by depth."""
import time

from test_access import add_client, build

URL = "https://docs.example/docs/"


def make_crawler(mem, pages):
    """A crawler that indexes ``pages`` (uri -> depth) and reports the depths, as make_crawler does."""
    def crawler(job, req):
        for uri in pages:
            mem.replace_source(req.collection, uri, "T", "0" * 64, [])
        return {"pages": len(pages), "added": len(pages), "depths": dict(pages)}
    return crawler


def wait(c, job_id, headers=None):
    for _ in range(100):
        job = c.get(f"/v1/jobs/{job_id}", headers=headers).json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def crawled(pages=None, with_holder=False):
    """A world with one crawl done: collection 'pets', a restricted 'private-a', and the saved web source."""
    holder = {}
    c, mem = build(crawler=lambda job, req: holder["crawler"](job, req))
    holder["crawler"] = make_crawler(mem, pages or {f"{URL}": 1, f"{URL}a": 2, f"{URL}a/b": 3})
    admin = add_client(mem, "root", "hip_root", admin=True)
    c.post("/v1/collections", json={"slug": "pets", "name": "Pets"})
    wait(c, c.post("/v1/crawl", json={"url": URL, "collection": "pets", "depth": 3}).json()["job_id"])
    return (c, mem, admin, holder) if with_holder else (c, mem, admin)


def test_a_crawl_is_remembered_with_its_settings_and_page_depths():
    c, _, _ = crawled()
    (web,) = c.get("/v1/web-sources").json()["web_sources"]
    assert (web["url"], web["collection"], web["depth"], web["engine"]) == (URL, "pets", 3, "static")
    assert (web["pages"], web["deepest"], web["last_status"]) == (3, 3, "ok")
    assert "depths" not in web["last_summary"]  # the per-page map is bookkeeping, not part of the result


def test_a_second_crawl_of_the_same_url_updates_the_one_web_source():
    c, _, _ = crawled()
    wait(c, c.post("/v1/crawl", json={"url": URL, "collection": "pets", "depth": 2}).json()["job_id"])
    (web,) = c.get("/v1/web-sources").json()["web_sources"]
    assert web["depth"] == 2


def test_change_depth_then_recrawl_uses_the_saved_settings():
    c, mem, _, holder = crawled(with_holder=True)
    web_id = c.get("/v1/web-sources").json()["web_sources"][0]["id"]
    assert c.patch(f"/v1/web-sources/{web_id}", json={"depth": 5, "match_pattern": "/docs/"}).json()["depth"] == 5
    seen = []
    inner = make_crawler(mem, {URL: 1})
    holder["crawler"] = lambda job, req: (seen.append((req.url, req.depth, req.match_pattern)), inner(job, req))[1]
    wait(c, c.post(f"/v1/web-sources/{web_id}/crawl").json()["job_id"])
    assert seen == [(URL, 5, "/docs/")]


def test_prune_counts_first_then_removes_only_the_deeper_pages():
    c, mem, _ = crawled()
    web_id = c.get("/v1/web-sources").json()["web_sources"][0]["id"]
    dry = c.post(f"/v1/web-sources/{web_id}/prune", json={"depth": 2}).json()
    assert dry == {"depth": 2, "dry_run": True, "pages": 1}
    assert len(mem.collections["pets"]) == 3  # nothing removed yet
    done = c.post(f"/v1/web-sources/{web_id}/prune", json={"depth": 2, "dry_run": False}).json()
    assert done["pages"] == 1 and set(mem.collections["pets"]) == {URL, f"{URL}a"}


def test_pages_without_a_depth_are_never_pruned():
    c, mem, _ = crawled()
    mem.replace_source("pets", f"{URL}old", "Old", "1" * 64, [])  # indexed before depths were recorded
    web_id = c.get("/v1/web-sources").json()["web_sources"][0]["id"]
    c.post(f"/v1/web-sources/{web_id}/prune", json={"depth": 1, "dry_run": False})
    assert f"{URL}old" in mem.collections["pets"]


def test_forgetting_a_web_source_keeps_its_pages():
    c, mem, _ = crawled()
    web_id = c.get("/v1/web-sources").json()["web_sources"][0]["id"]
    assert c.delete(f"/v1/web-sources/{web_id}").status_code == 204
    assert c.get("/v1/web-sources").json()["web_sources"] == [] and len(mem.collections["pets"]) == 3


def test_a_failed_crawl_is_recorded_on_the_web_source():
    c, mem = build(crawler=lambda job, req: (_ for _ in ()).throw(ValueError("boom")))
    c.post("/v1/collections", json={"slug": "pets", "name": "Pets"})
    assert wait(c, c.post("/v1/crawl", json={"url": URL, "collection": "pets"}).json()["job_id"])["status"] == "error"
    web = c.get("/v1/web-sources").json()["web_sources"][0]
    assert web["last_status"] == "error" and "boom" in web["last_summary"]["error"]


def test_web_sources_follow_the_access_rules():
    c, mem = build(crawler=lambda job, req: {"depths": {}})
    admin = add_client(mem, "root", "hip_root", admin=True)
    reader = add_client(mem, "reader", "hip_reader", read=["private-a"])
    nobody = add_client(mem, "nobody", "hip_nobody")
    c.post("/v1/collections", json={"slug": "private-a", "name": "A", "visibility": "restricted"}, headers=admin)
    wait(c, c.post("/v1/crawl", json={"url": URL, "collection": "private-a"}, headers=admin).json()["job_id"], admin)
    web_id = c.get("/v1/web-sources", headers=admin).json()["web_sources"][0]["id"]
    # hidden: a caller without a grant sees none, and every action looks like "not found"
    assert c.get("/v1/web-sources", headers=nobody).json()["web_sources"] == []
    for verb, path, body in (("patch", "", {"depth": 1}), ("delete", "", None), ("post", "/crawl", None),
                             ("post", "/prune", {"depth": 1})):
        r = getattr(c, verb)(f"/v1/web-sources/{web_id}{path}", headers=nobody, **({"json": body} if body else {}))
        assert r.status_code == 404, (verb, path)
    # denied: may read but not write
    assert len(c.get("/v1/web-sources", headers=reader).json()["web_sources"]) == 1
    for verb, path, body in (("patch", "", {"depth": 1}), ("delete", "", None), ("post", "/crawl", None),
                             ("post", "/prune", {"depth": 1, "dry_run": False})):
        r = getattr(c, verb)(f"/v1/web-sources/{web_id}{path}", headers=reader, **({"json": body} if body else {}))
        assert r.status_code == 403, (verb, path)
