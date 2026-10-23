"""Per-collection access control (HIPPO-E06-F0): restricted collections are invisible without a grant."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from test_api import KeywordEmbedder, MemStore

from hippocampus import auth
from hippocampus.api import create_app
from hippocampus.jobs import JobRegistry


def build(require_auth=False, crawler=None):
    mem = MemStore()

    @contextmanager
    def factory():
        yield mem

    client = TestClient(create_app(factory, KeywordEmbedder(), JobRegistry(), crawler, require_auth=require_auth))
    return client, mem


def add_client(mem, name, token, admin=False, read=(), write=()):
    mem.create_client(name, auth.hash_token(token), admin, list(read), list(write), None)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world():
    """A shared 'pets' collection and a restricted 'jstories-a' whose chunk is the best match for 'cat'."""
    c, mem = build()
    admin = add_client(mem, "root", "hip_root", admin=True)
    kobold = add_client(mem, "kobold", "hip_kobold", read=["jstories-*"], write=["jstories-*"])
    other = add_client(mem, "other", "hip_other", read=["jstories-b"])
    assert c.post("/v1/collections", json={"slug": "pets", "name": "Pets"}).status_code == 201
    assert c.post("/v1/collections", json={"slug": "jstories-a", "name": "A", "visibility": "restricted"},
                  headers=admin).status_code == 201
    c.post("/v1/ingest/file", json={"collection": "pets", "uri": "dogs.md", "text": "# D\nA dog."})
    c.post("/v1/ingest/file", json={"collection": "jstories-a", "uri": "jstories://a/ch-1",
                                    "text": "# Ch1\ncat cat cat"}, headers=kobold)
    return c, mem, {"admin": admin, "kobold": kobold, "other": other}


# --- grant matching ---

def test_grant_globs_are_exact_about_the_prefix():
    assert auth.matches(["jstories-*"], "jstories-a")
    assert not auth.matches(["jstories-*"], "jstories")
    assert not auth.matches(["jstories-*"], "xjstories-a")
    assert auth.matches(["pets"], "pets") and not auth.matches(["pets"], "pets2")


def test_validate_grants_refuses_a_bare_wildcard_and_junk():
    assert auth.validate_grants(["a", "jstories-*", "a"]) == ["a", "jstories-*"]
    for bad in ("*", "**", "Upper", "a b", "../x", ""):
        with pytest.raises(ValueError):
            auth.validate_grants([bad])


def test_tokens_are_random_and_only_the_hash_is_stored():
    a, b = auth.new_token(), auth.new_token()
    assert a != b and a.startswith("hip_") and len(a) > 40
    assert auth.hash_token(a) != a and len(auth.hash_token(a)) == 64


# --- visibility ---

def test_unscoped_search_never_returns_restricted_chunks_even_when_they_are_the_best_match(world):
    c, _, _ = world
    hits = c.post("/v1/search", json={"query": "cat", "top_k": 10}).json()["results"]
    assert hits and {h["collection"] for h in hits} == {"pets"}


def test_named_restricted_collection_looks_missing_without_a_grant(world):
    c, _, tok = world
    missing = c.post("/v1/search", json={"query": "cat", "collections": ["no-such"]})
    hidden = c.post("/v1/search", json={"query": "cat", "collections": ["jstories-a"]})
    assert hidden.status_code == missing.status_code == 404
    assert c.get("/v1/collections/jstories-a/sources").status_code == 404
    assert c.post("/v1/search", json={"query": "cat", "collections": ["jstories-a"]},
                  headers=tok["other"]).status_code == 404  # a grant for a different story is not enough


def test_granted_client_and_admin_can_read_it(world):
    c, _, tok = world
    for who in ("kobold", "admin"):
        hits = c.post("/v1/search", json={"query": "cat"}, headers=tok[who]).json()["results"]
        assert hits[0]["collection"] == "jstories-a"
        assert c.get("/v1/collections/jstories-a/sources", headers=tok[who]).status_code == 200


def test_listing_and_status_counts_respect_the_principal(world):
    c, _, tok = world
    assert [x["slug"] for x in c.get("/v1/collections").json()["collections"]] == ["pets"]
    assert c.get("/v1/status").json()["collections"] == 1
    assert [x["slug"] for x in c.get("/v1/collections", headers=tok["kobold"]).json()["collections"]] == ["jstories-a", "pets"]
    assert c.get("/v1/status", headers=tok["kobold"]).json()["collections"] == 2
    assert c.get("/v1/collections", headers=tok["admin"]).json()["collections"][0]["visibility"] == "restricted"


# --- writes ---

def test_restricted_writes_need_a_write_grant(world):
    c, _, tok = world
    body = {"collection": "jstories-a", "uri": "jstories://a/ch-2", "text": "# Ch2\nmore"}
    assert c.post("/v1/ingest/file", json=body).status_code == 404            # anonymous: hidden
    assert c.post("/v1/ingest/file", json=body, headers=tok["other"]).status_code == 404
    read_only = add_client(world[1], "reader", "hip_reader", read=["jstories-a"])
    assert c.post("/v1/ingest/file", json=body, headers=read_only).status_code == 403
    assert c.post("/v1/ingest/file", json=body, headers=tok["kobold"]).status_code == 200


def test_private_uris_cannot_be_indexed_into_a_shared_collection(world):
    c, _, tok = world
    r = c.post("/v1/ingest/file", json={"collection": "pets", "uri": "jstories://a/ch-9", "text": "x"},
               headers=tok["admin"])
    assert r.status_code == 403 and "restricted" in r.json()["detail"]


def test_jstories_collections_must_be_restricted(world):
    c, _, tok = world
    r = c.post("/v1/collections", json={"slug": "jstories-z", "name": "Z"}, headers=tok["admin"])
    assert r.status_code == 422
    r = c.patch("/v1/collections/jstories-a", json={"visibility": "shared"}, headers=tok["admin"])
    assert r.status_code == 422


def test_only_admins_create_restricted_or_change_visibility(world):
    c, _, tok = world
    assert c.post("/v1/collections", json={"slug": "secret", "name": "S", "visibility": "restricted"},
                  headers=tok["kobold"]).status_code == 403
    assert c.patch("/v1/collections/pets", json={"visibility": "restricted"}, headers=tok["kobold"]).status_code == 403
    assert c.patch("/v1/collections/pets", json={"visibility": "restricted"}, headers=tok["admin"]).status_code == 200
    assert c.get("/v1/collections").json()["collections"] == []  # flipped: gone for anonymous
    assert c.patch("/v1/collections/pets", json={"visibility": "shared"}, headers=tok["admin"]).status_code == 200
    assert [x["slug"] for x in c.get("/v1/collections").json()["collections"]] == ["pets"]


def test_deleting_a_restricted_collection_needs_admin(world):
    c, mem, tok = world
    assert c.delete("/v1/collections/jstories-a").status_code == 404
    assert c.delete("/v1/collections/jstories-a", headers=tok["kobold"]).status_code == 403
    assert c.delete("/v1/collections/jstories-a", headers=tok["admin"]).status_code == 204
    assert "jstories-a" not in mem.collections


def test_creating_a_name_that_exists_as_hidden_does_not_confirm_it(world):
    c, _, _ = world
    taken = c.post("/v1/collections", json={"slug": "jstories-a", "name": "x"})
    assert taken.status_code == 422  # private prefix rule fires first
    c.patch("/v1/collections/pets", json={"visibility": "restricted"}, headers={"Authorization": "Bearer hip_root"})
    assert c.post("/v1/collections", json={"slug": "pets", "name": "x"}).status_code == 403


# --- tokens ---

def test_bad_missing_scheme_and_revoked_tokens_are_401_not_anonymous(world):
    c, mem, _ = world
    assert c.get("/v1/collections", headers={"Authorization": "Bearer hip_nope"}).status_code == 401
    # credentials that are not ours (a proxy's JWT, Basic) carry no privilege and are ignored, not errors
    assert c.get("/v1/collections", headers={"Authorization": "Basic abc"}).status_code == 200
    assert c.get("/v1/collections", headers={"Authorization": "Bearer eyJhbGciOi.proxy.jwt"}).status_code == 200
    assert c.get("/v1/collections", headers={"X-Hippo-Token": "hip_nope"}).status_code == 401
    assert c.get("/v1/collections", headers={"Authorization": "Bearer hip_kobold"}).status_code == 200
    mem.revoke_client("kobold")
    assert c.get("/v1/collections", headers={"Authorization": "Bearer hip_kobold"}).status_code == 401


def test_whoami(world):
    c, _, tok = world
    assert c.get("/v1/whoami").json()["name"] == "anonymous"
    me = c.get("/v1/whoami", headers=tok["kobold"]).json()
    assert me["name"] == "kobold" and me["write"] == ["jstories-*"] and not me["admin"]


# --- client management ---

def test_client_admin_endpoints_need_admin(world):
    c, _, tok = world
    for call in (lambda h: c.get("/v1/clients", headers=h),
                 lambda h: c.post("/v1/clients", json={"name": "x"}, headers=h),
                 lambda h: c.patch("/v1/clients/kobold", json={"admin": True}, headers=h),
                 lambda h: c.delete("/v1/clients/kobold", headers=h)):
        assert call({}).status_code == 403
        assert call(tok["kobold"]).status_code == 403


def test_create_client_shows_the_token_once_and_it_works(world):
    c, _, tok = world
    r = c.post("/v1/clients", json={"name": "scribe", "read": ["jstories-a"]}, headers=tok["admin"])
    assert r.status_code == 201
    token = r.json()["token"]
    assert "token" not in str(c.get("/v1/clients", headers=tok["admin"]).json())
    assert c.post("/v1/search", json={"query": "cat", "collections": ["jstories-a"]},
                  headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert c.post("/v1/clients", json={"name": "scribe"}, headers=tok["admin"]).status_code == 409
    assert c.post("/v1/clients", json={"name": "Bad Name"}, headers=tok["admin"]).status_code == 422
    assert c.post("/v1/clients", json={"name": "wide", "read": ["*"]}, headers=tok["admin"]).status_code == 422


def test_update_and_revoke_client_and_last_admin_is_protected(world):
    c, _, tok = world
    assert c.patch("/v1/clients/other", json={"read": ["jstories-a"]}, headers=tok["admin"]).status_code == 200
    assert c.post("/v1/search", json={"query": "cat", "collections": ["jstories-a"]}, headers=tok["other"]).status_code == 200
    assert c.delete("/v1/clients/other", headers=tok["admin"]).status_code == 204
    assert c.get("/v1/collections", headers=tok["other"]).status_code == 401
    assert c.delete("/v1/clients/root", headers=tok["admin"]).status_code == 409
    assert c.patch("/v1/clients/root", json={"admin": False}, headers=tok["admin"]).status_code == 409
    assert c.delete("/v1/clients/ghost", headers=tok["admin"]).status_code == 404


# --- jobs ---

def test_jobs_belong_to_their_owner(world):
    def crawler(job, req):
        job.log("fetched")
        return {"pages": 1}

    c, mem = build(crawler=crawler)
    admin = add_client(mem, "root", "hip_root", admin=True)
    owner = add_client(mem, "kobold", "hip_kobold", write=["jstories-*"])
    c.post("/v1/collections", json={"slug": "jstories-a", "name": "A", "visibility": "restricted"}, headers=admin)
    jid = c.post("/v1/crawl", json={"url": "https://x.test", "collection": "jstories-a"}, headers=owner).json()["job_id"]
    assert c.post("/v1/crawl", json={"url": "https://x.test", "collection": "jstories-a"}).status_code == 404
    assert c.get(f"/v1/jobs/{jid}").status_code == 404
    assert c.get(f"/v1/jobs/{jid}/events").status_code == 404
    assert c.get(f"/v1/jobs/{jid}", headers=owner).status_code == 200
    assert c.get(f"/v1/jobs/{jid}", headers=admin).status_code == 200


# --- require_auth (phase C) ---

def test_require_auth_makes_shared_writes_need_a_grant():
    c, mem = build(require_auth=True)
    admin = add_client(mem, "root", "hip_root", admin=True)
    docs = add_client(mem, "docs", "hip_docs", write=["pets"])
    assert c.post("/v1/collections", json={"slug": "pets", "name": "P"}).status_code == 403
    assert c.post("/v1/collections", json={"slug": "pets", "name": "P"}, headers=admin).status_code == 201
    body = {"collection": "pets", "uri": "a.md", "text": "# A\ncat"}
    assert c.post("/v1/ingest/file", json=body).status_code == 403
    assert c.post("/v1/ingest/file", json=body, headers=docs).status_code == 200
    assert c.post("/v1/search", json={"query": "cat"}).json()["results"]  # reading shared stays open


def test_x_hippo_token_wins_over_a_proxy_authorization_header(world):
    c, _, _ = world
    both = {"Authorization": "Bearer eyJproxy.jwt.value", "X-Hippo-Token": "hip_kobold"}
    assert c.get("/v1/whoami", headers=both).json()["name"] == "kobold"
    assert c.get("/v1/whoami", headers={"Authorization": "Bearer eyJproxy.jwt.value"}).json()["name"] == "anonymous"


def test_rotate_gives_a_new_token_and_kills_the_old_one(world):
    c, _, tok = world
    assert c.post("/v1/clients/kobold/rotate").status_code == 403                       # anonymous
    assert c.post("/v1/clients/kobold/rotate", headers=tok["kobold"]).status_code == 403  # not admin
    r = c.post("/v1/clients/kobold/rotate", headers=tok["admin"])
    assert r.status_code == 200 and r.json()["name"] == "kobold" and r.json()["token"].startswith("hip_")
    new = {"Authorization": f"Bearer {r.json()['token']}"}
    assert c.get("/v1/collections", headers=tok["kobold"]).status_code == 401           # old token is dead
    me = c.get("/v1/whoami", headers=new).json()
    assert me["name"] == "kobold" and me["write"] == ["jstories-*"]                     # grants survived
    assert c.post("/v1/search", json={"query": "cat"}, headers=new).json()["results"][0]["collection"] == "jstories-a"
    assert "token" not in str(c.get("/v1/clients", headers=tok["admin"]).json())


def test_rotate_unknown_or_revoked_client_is_404(world):
    c, mem, tok = world
    assert c.post("/v1/clients/ghost/rotate", headers=tok["admin"]).status_code == 404
    mem.revoke_client("other")
    assert c.post("/v1/clients/other/rotate", headers=tok["admin"]).status_code == 404


def test_an_admin_can_rotate_their_own_token(world):
    c, _, tok = world
    token = c.post("/v1/clients/root/rotate", headers=tok["admin"]).json()["token"]
    assert c.get("/v1/clients", headers=tok["admin"]).status_code == 401
    assert c.get("/v1/clients", headers={"X-Hippo-Token": token}).status_code == 200
