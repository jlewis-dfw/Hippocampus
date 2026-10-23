import os
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("database")  # a throwaway database unless HIPPO_TEST_DSN names one (see conftest.py)


def test_cli_roundtrip(monkeypatch, capsys):
    import psycopg

    from hippocampus import admin, auth

    dsn = os.environ["HIPPO_TEST_DSN"]
    monkeypatch.setenv("HIPPO_DSN", dsn)
    name, slug = f"cli-{uuid.uuid4().hex[:8]}", f"jstories-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO hippocampus.collections (slug, name) VALUES (%s, %s)", (slug, slug))
        try:
            assert admin.main(["create-client", "--name", name, "--read", "jstories-*"]) == 0
            out = capsys.readouterr().out
            token = out.split("store it in the vault): ")[1].strip()
            assert token.startswith("hip_")
            row = conn.execute("SELECT token_sha256 FROM hippocampus.api_clients WHERE name=%s", (name,)).fetchone()
            assert row[0] == auth.hash_token(token) and token not in row[0]
            assert admin.main(["create-client", "--name", name]) == 1               # duplicate
            assert admin.main(["create-client", "--name", "x", "--read", "*"]) == 2  # bare wildcard
            assert admin.main(["set-visibility", slug, "restricted"]) == 0
            assert admin.main(["set-visibility", slug, "shared"]) == 2               # jstories* stays private
            assert admin.main(["list-clients"]) == 0 and name in capsys.readouterr().out
            assert admin.main(["rotate-client", name]) == 0
            new = capsys.readouterr().out.split("store it in the vault): ")[1].strip()
            assert new != token and new.startswith("hip_")
            row = conn.execute("SELECT token_sha256 FROM hippocampus.api_clients WHERE name=%s", (name,)).fetchone()
            assert row[0] == auth.hash_token(new)
            assert admin.main(["rotate-client", "no-such-client"]) == 1
            assert admin.main(["revoke-client", name]) == 0 and admin.main(["revoke-client", name]) == 1
        finally:
            conn.execute("DELETE FROM hippocampus.api_clients WHERE name = %s", (name,))
            conn.execute("DELETE FROM hippocampus.collections WHERE slug = %s", (slug,))
