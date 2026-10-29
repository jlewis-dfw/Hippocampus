"""The deterministic helpers behind the slash commands: backup files, folder scan, migration numbering, docs check."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backup_recognises_a_complete_dump_and_rejects_a_cut_off_one(tmp_path):
    backup = load("backup")
    good, cut = tmp_path / "hippocampus-20260101-000000.sql", tmp_path / "hippocampus-20260102-000000.sql"
    good.write_bytes(b"-- data\n\n--\n-- PostgreSQL database dump complete\n--\n")
    cut.write_bytes(b"-- data\nCOPY x FROM stdin;\n" + b"row\n" * 1000)
    assert backup.complete(good) and not backup.complete(cut)
    assert [p.name for p in backup.dumps(tmp_path)] == [cut.name, good.name]  # newest first


def test_backup_restore_test_refuses_an_incomplete_dump_before_starting_anything(tmp_path):
    cut = tmp_path / "hippocampus-1.sql"
    cut.write_text("-- cut off")
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "backup.py"), "restore-test", str(cut)], capture_output=True, text=True)
    assert res.returncode == 1 and "incomplete" in res.stderr


def test_index_folder_skips_junk_folders_and_other_extensions(tmp_path):
    mod = load("index_folder")
    for rel in ("a.md", "sub/b.txt", ".git/c.md", "node_modules/d.md", "e.png", "sub/f.py"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert [rel for rel, _ in mod.collect(tmp_path, mod.DEFAULT_EXT)] == ["a.md", "sub/b.txt"]
    assert "sub/f.py" in [rel for rel, _ in mod.collect(tmp_path, (*mod.DEFAULT_EXT, ".py"))]


def test_index_folder_splits_big_runs_into_batches(tmp_path):
    mod = load("index_folder")
    files = []
    for i in range(mod.BATCH_FILES + 5):
        p = tmp_path / f"{i}.md"
        p.write_text("x")
        files.append((p.name, p))
    sizes = [len(b) for b in mod.batches(files)]
    assert sizes == [mod.BATCH_FILES, 5]


def test_new_migration_numbers_after_the_highest_and_rejects_bad_names(tmp_path, monkeypatch):
    mod = load("new_migration")
    (tmp_path / "0001_a.sql").write_text("")
    (tmp_path / "0002_b.sql").write_text("")
    monkeypatch.setattr(mod, "FOLDER", tmp_path)
    monkeypatch.setattr(sys, "argv", ["new_migration.py", "add_tags"])
    assert mod.main() == 0 and (tmp_path / "0003_add_tags.sql").is_file()
    monkeypatch.setattr(sys, "argv", ["new_migration.py", "Bad Name"])
    assert mod.main() == 2


def test_docs_check_flags_a_missing_file_a_missing_setting_and_a_missing_route(tmp_path, monkeypatch, capsys):
    mod = load("docs_check")
    monkeypatch.setattr(mod, "ROOT", ROOT)
    page = ROOT / "tests" / "unit" / "_docs_check_sample.md"
    fake = "HIPPO_" + "NO_SUCH_SETTING"  # built here so this file does not itself define it
    page.write_text(f"See `scripts/nope.py`, {fake}, /v1/not-a-route and `scripts/backup.py`, /v1/search.\n")
    try:
        monkeypatch.setattr(sys, "argv", ["docs_check.py", page.relative_to(ROOT).as_posix()])
        assert mod.main() == 1
    finally:
        page.unlink()
    out = capsys.readouterr().out
    assert "scripts/nope.py" in out and fake in out and "/v1/not-a-route" in out
    assert "backup.py" not in out and "/v1/search" not in out
