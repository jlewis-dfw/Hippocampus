import pytest

from hippocampus.repo_ingest import ingest_pack, parse_pack, scan_pack, scan_secrets

from test_api import KeywordEmbedder, MemStore, make_client

# Fake secrets are assembled at runtime so this file itself holds no secret-shaped literal.
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_PEM = "-----BEGIN RSA PRIVATE" + " KEY-----\nMIIfake\n-----END RSA PRIVATE KEY-----"
FAKE_BEARER = "Authorization: Bearer " + "abcdef0123456789abcdef0123456789"


def xml_pack(files):
    body = "".join(f'<file path="{p}">\n{c}\n</file>\n\n' for p, c in files.items())
    return f"This file is a merged representation.\n<files>\n{body}</files>\n"


def md_pack(files):
    body = "".join(f"## File: {p}\n```\n{c}\n```\n\n" for p, c in files.items())
    return f"# Files\n\n{body}"


CLEAN = {"README.md": "# Cat service\n\nPurrs.", "src/app.py": "def f():\n    return 'dog'\n"}


class RecordingEmbedder(KeywordEmbedder):
    def __init__(self):
        self.seen = []

    def embed(self, texts):
        self.seen.extend(texts)
        return super().embed(texts)


@pytest.mark.parametrize("pack", [xml_pack, md_pack])
def test_parse_both_formats(pack):
    assert dict(parse_pack(pack(CLEAN))) == CLEAN


def test_unknown_format_is_rejected():
    with pytest.raises(ValueError):
        parse_pack("just some text")


@pytest.mark.parametrize("text,rule", [
    (f"key = {FAKE_AWS}", "aws-access-key-id"),
    (FAKE_PEM, "pem-private-key"),
    (FAKE_BEARER, "bearer-token"),
    ('password = "hunter2hunter2"', "credential-assignment"),
])
def test_scan_detects_secret_shapes(text, rule):
    assert rule in scan_secrets(text)


def test_scan_ignores_ordinary_code():
    assert scan_secrets("password = settings.password\ntoken = get_token(user)\nBearer scheme docs") == []


def test_secret_file_never_reaches_the_embedder():
    pack = xml_pack({**CLEAN, "src/leak.py": f"KEY = '{FAKE_AWS}'\n", "ops/.env": "A=1", "x/id_rsa": "y"})
    emb, store = RecordingEmbedder(), MemStore()
    store.create_collection("c", "c", None)
    out = ingest_pack("c", "repo1", pack, store, emb)
    assert out["files"] == 2 and out["added"] == 2
    rejected = {r["path"]: r["reasons"] for r in out["rejected"]}
    assert rejected == {"src/leak.py": ["aws-access-key-id"], "ops/.env": ["secret-file-name"],
                        "x/id_rsa": ["secret-file-name"]}
    assert not any(FAKE_AWS in t for t in emb.seen)
    assert FAKE_AWS not in repr(out)  # the report names rules, never the matched text
    assert set(store.collections["c"]) == {"repo://repo1/README.md", "repo://repo1/src/app.py"}


def test_clean_pack_is_ingested_and_incremental():
    store = MemStore()
    store.create_collection("c", "c", None)
    first = ingest_pack("c", "r", xml_pack(CLEAN), store, KeywordEmbedder())
    assert first["added"] == 2 and first["rejected"] == []
    second = ingest_pack("c", "r", xml_pack(CLEAN), store, KeywordEmbedder())
    assert second["unchanged"] == 2 and second["added"] == 0 and second["removed"] == 0


def test_deleted_and_newly_flagged_files_are_removed_but_other_sources_kept():
    store = MemStore()
    store.create_collection("c", "c", None)
    store.replace_source("c", "https://elsewhere/x", "x", "h", [])
    ingest_pack("c", "r", xml_pack(CLEAN), store, KeywordEmbedder())
    leaked = {"README.md": CLEAN["README.md"], "src/app.py": f"x = '{FAKE_AWS}'"}
    out = ingest_pack("c", "r", xml_pack(leaked), store, KeywordEmbedder())
    assert out["removed"] == 1  # the previously embedded file is purged once it carries a secret
    assert set(store.collections["c"]) == {"repo://r/README.md", "https://elsewhere/x"}


def test_infra_paths_and_repo_are_excluded():
    scan = scan_pack("r", xml_pack({"private-infra/docs/a.md": "# ips", "ok.md": "# ok"}))
    assert list(scan.documents) == ["repo://r/ok.md"]
    assert scan.rejected[0]["reasons"] == ["excluded-source"]
    with pytest.raises(ValueError):
        ingest_pack("c", "private-infra", xml_pack(CLEAN), MemStore(), KeywordEmbedder())


def test_code_with_backticks_is_fenced_safely():
    scan = scan_pack("r", xml_pack({"a.py": 'x = """```inner```"""'}))
    text = scan.documents["repo://r/a.py"].text
    assert text.startswith("# a.py\n\n````python\n") and text.endswith("\n````")


def test_api_endpoint_runs_job_and_validates():
    c, mem = make_client()
    assert c.post("/v1/ingest/repo", json={"collection": "d", "repo": "r", "pack": xml_pack(CLEAN)}).status_code == 404
    c.post("/v1/collections", json={"slug": "d", "name": "d"})
    assert c.post("/v1/ingest/repo", json={"collection": "d", "repo": "r", "pack": "nope"}).status_code == 422
    assert c.post("/v1/ingest/repo", json={"collection": "d", "repo": "private-infra",
                                            "pack": xml_pack(CLEAN)}).status_code == 403
    pack = xml_pack({**CLEAN, "leak.py": FAKE_AWS})
    jid = c.post("/v1/ingest/repo", json={"collection": "d", "repo": "r", "pack": pack}).json()["job_id"]
    c.get(f"/v1/jobs/{jid}/events")
    job = c.get(f"/v1/jobs/{jid}").json()
    assert job["status"] == "done" and job["result"]["files"] == 2
    assert job["result"]["rejected"] == [{"path": "leak.py", "reasons": ["aws-access-key-id"]}]
    assert FAKE_AWS not in repr(job)
