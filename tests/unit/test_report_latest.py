"""tests/hippo_report.py publish_latest: ``reports/latest`` is a real folder with a copy of the run, free of this machine's paths."""
from __future__ import annotations

import json
from pathlib import Path

from hippo_report import ROOT, publish_latest


def make_run(tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    run = reports / "20260101000000"
    run.mkdir(parents=True)
    trace = f"File \"{ROOT}/src/hippocampus/x.py\", line 1\n  at {Path.home()}/elsewhere"
    (run / "report.html").write_text(f"<pre>{trace}</pre>", encoding="utf-8")
    (run / "results.json").write_text(json.dumps({"detail": trace, "run_id": "20260101000000"}), encoding="utf-8")
    (run / "junit.xml").write_text(f"<x file='{ROOT}/tests/a.py'/>", encoding="utf-8")
    return reports, run


def test_latest_is_a_real_folder_with_the_runs_files_and_no_local_paths(tmp_path: Path) -> None:
    reports, run = make_run(tmp_path)
    latest = publish_latest(run, reports)
    assert latest.is_dir() and not latest.is_symlink()
    assert sorted(p.name for p in latest.iterdir()) == ["junit.xml", "report.html", "results.json"]
    for p in latest.iterdir():
        text = p.read_text(encoding="utf-8")
        assert str(ROOT) not in text and str(Path.home()) not in text
    assert "<repo>/src/hippocampus/x.py" in (latest / "report.html").read_text(encoding="utf-8")
    assert json.loads((latest / "results.json").read_text(encoding="utf-8"))["detail"].endswith("~/elsewhere")
    assert str(ROOT) in (run / "report.html").read_text(encoding="utf-8")  # the run's own folder keeps the originals


def test_an_old_symlink_or_old_copy_is_replaced(tmp_path: Path) -> None:
    reports, run = make_run(tmp_path)
    old = reports / "20250101000000"
    old.mkdir()
    link = reports / "latest"
    try:
        link.symlink_to(old.name, target_is_directory=True)
    except OSError:
        link.mkdir()  # no symlinks on this system: an old copy plays the same part
    (old / "stale.txt").write_text("x", encoding="utf-8")
    publish_latest(run, reports)
    assert not link.is_symlink() and link.is_dir() and not (link / "stale.txt").exists()
    assert (old / "stale.txt").exists()  # what the old link pointed at is untouched
    (run / "report.html").write_text("second", encoding="utf-8")
    publish_latest(run, reports)  # and a second publish over a real folder works
    assert (link / "report.html").read_text(encoding="utf-8") == "second"
