"""migrate, healthcheck, embedder and admin: the small operational modules, tested without a database or a model."""
import runpy
import sys
import types
from contextlib import contextmanager

import pytest
from test_api import MemStore

from hippocampus import admin, embedder, migrate

# --- migrate


def _write(folder, *names):
    for n in names:
        (folder / n).write_text(f"-- {n}")
    return folder


def test_migration_files_validates_names_and_numbers(tmp_path):
    with pytest.raises(SystemExit, match="no migrations"):
        migrate.migration_files(tmp_path)
    bad = tmp_path
    (bad / "oops.sql").write_text("")
    with pytest.raises(SystemExit, match="must look like"):
        migrate.migration_files(bad)
    (bad / "oops.sql").unlink()
    _write(bad, "0001_a.sql", "0003_c.sql")
    with pytest.raises(SystemExit, match="without gaps"):
        migrate.migration_files(bad)
    (bad / "0003_c.sql").rename(bad / "0002_b.sql")
    assert [f.name for f in migrate.migration_files(bad)] == ["0001_a.sql", "0002_b.sql"]


def test_migrations_dir_from_env_or_checkout(monkeypatch, tmp_path):
    monkeypatch.setenv("HIPPO_MIGRATIONS_DIR", str(tmp_path))
    assert migrate.migrations_dir() == tmp_path
    monkeypatch.delenv("HIPPO_MIGRATIONS_DIR")
    assert migrate.migrations_dir().name == "migrations" and migrate.migrations_dir().is_dir()


class FakeConn:
    def __init__(self, done=()):
        self.done, self.executed, self.in_tx = list(done), [], 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return [(n,) for n in self.done] if "SELECT name" in sql else None

    @contextmanager
    def transaction(self):
        self.in_tx += 1
        yield

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_apply_all_skips_recorded_migrations(tmp_path):
    _write(tmp_path, "0001_a.sql", "0002_b.sql")
    conn = FakeConn(done=["0001_a.sql"])
    assert migrate.apply_all(conn, tmp_path) == ["0002_b.sql"]
    assert conn.in_tx == 1
    assert any(p == ("0002_b.sql",) for _, p in conn.executed)
    assert migrate.apply_all(FakeConn(done=["0001_a.sql", "0002_b.sql"]), tmp_path) == []


def _fake_psycopg(monkeypatch, connect):
    mod = types.ModuleType("psycopg")

    class OperationalError(Exception):
        pass

    mod.OperationalError = OperationalError
    mod.connect = lambda dsn, autocommit: connect(OperationalError)
    monkeypatch.setitem(sys.modules, "psycopg", mod)
    return mod


def test_migrate_main_requires_dsn(monkeypatch, capsys):
    monkeypatch.delenv("HIPPO_DSN", raising=False)
    assert migrate.main() == 2 and "HIPPO_DSN" in capsys.readouterr().err


def test_migrate_main_applies_and_reports(monkeypatch, tmp_path, capsys):
    _write(tmp_path, "0001_a.sql")
    monkeypatch.setenv("HIPPO_DSN", "postgresql://x")
    monkeypatch.setenv("HIPPO_MIGRATIONS_DIR", str(tmp_path))
    _fake_psycopg(monkeypatch, lambda err: FakeConn())
    assert migrate.main() == 0 and "0001_a.sql" in capsys.readouterr().out
    _fake_psycopg(monkeypatch, lambda err: FakeConn(done=["0001_a.sql"]))
    assert migrate.main() == 0 and "none pending" in capsys.readouterr().out


def test_migrate_main_retries_then_gives_up(monkeypatch, capsys):
    monkeypatch.setenv("HIPPO_DSN", "postgresql://x")
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)

    def refuse(err):
        raise err("connection refused")

    _fake_psycopg(monkeypatch, refuse)
    assert migrate.main() == 1
    assert len(sleeps) == 30 and "cannot reach the database" in capsys.readouterr().err


# --- healthcheck

def test_healthcheck_exit_codes(monkeypatch, capsys):
    class Resp:
        def close(self):
            pass

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Resp())
    runpy.run_module("hippocampus.healthcheck", run_name="__main__")  # answers: falls through, exit 0

    def down(*a, **k):
        raise OSError("refused")

    monkeypatch.setattr("urllib.request.urlopen", down)
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("hippocampus.healthcheck", run_name="__main__")
    assert exc.value.code == 1 and "refused" in capsys.readouterr().err


# --- embedder (fastembed replaced by a stand-in so no model is downloaded)

def _fake_fastembed(monkeypatch, dims=embedder.DIMENSIONS):
    made = []

    class TextEmbedding:
        def __init__(self, **kw):
            made.append(kw)

        def embed(self, texts):
            return ([i * 0.001] * dims for i, _ in enumerate(texts))

    mod = types.ModuleType("fastembed")
    mod.TextEmbedding = TextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", mod)
    return made


def test_fast_embedder_loads_lazily_once(monkeypatch):
    made = _fake_fastembed(monkeypatch)
    e = embedder.FastEmbedder(cache_dir="/cache")
    assert e.embed([]) == [] and made == []  # nothing loaded for an empty batch
    vectors = e.embed(["a", "b"])
    e.embed(["c"])
    assert len(vectors) == 2 and len(vectors[0]) == embedder.DIMENSIONS
    assert len(made) == 1 and made[0]["cache_dir"] == "/cache" and made[0]["providers"] == ["CPUExecutionProvider"]


def test_fast_embedder_rejects_wrong_dimensions(monkeypatch):
    _fake_fastembed(monkeypatch, dims=10)
    with pytest.raises(ValueError, match="384-dim"):
        embedder.FastEmbedder().embed(["a"])


# --- admin CLI against an in-memory store

@pytest.fixture
def mem(monkeypatch):
    store = MemStore()
    store.create_collection("docs", "docs", None)
    monkeypatch.setenv("HIPPO_DSN", "postgresql://x")

    @contextmanager
    def factory():
        yield store

    monkeypatch.setattr(admin, "connect_store", lambda dsn: factory)
    return store


def test_admin_requires_dsn(monkeypatch, capsys):
    monkeypatch.delenv("HIPPO_DSN", raising=False)
    assert admin.main(["list-clients"]) == 2 and "HIPPO_DSN" in capsys.readouterr().err


def test_admin_create_validates_input(mem, capsys):
    assert admin.main(["create-client", "--name", "Bad Name!"]) == 2
    assert admin.main(["create-client", "--name", "ok", "--read", "*"]) == 2
    assert admin.main(["create-client", "--name", "ok", "--admin", "--note", "n"]) == 0
    assert admin.main(["create-client", "--name", "ok"]) == 1
    err = capsys.readouterr().err
    assert "invalid client name" in err and "already exists" in err


def test_admin_rotate_revoke_list_and_visibility(mem, capsys):
    assert admin.main(["rotate-client", "ghost"]) == 1
    assert admin.main(["revoke-client", "ghost"]) == 1
    assert admin.main(["create-client", "--name", "bot", "--read", "docs"]) == 0
    capsys.readouterr()
    assert admin.main(["rotate-client", "bot"]) == 0
    assert "hip_" in capsys.readouterr().out
    assert admin.main(["list-clients"]) == 0 and "active" in capsys.readouterr().out
    assert admin.main(["revoke-client", "bot"]) == 0
    assert admin.main(["list-clients"]) == 0 and "REVOKED" in capsys.readouterr().out
    assert admin.main(["set-visibility", "docs", "restricted"]) == 0
    assert admin.main(["set-visibility", "missing", "restricted"]) == 1
    assert mem.visibility["docs"] == "restricted"
