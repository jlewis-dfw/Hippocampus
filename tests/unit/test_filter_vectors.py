"""The shared vectors: sync_core.py and the dashboard's folder.js must both give these answers (the JS side runs in
tests/js/check_vectors.js)."""
import json
from pathlib import Path

import pytest

from hippocampus.sync_core import FileFilter, PathError, normalize_path, normalize_text

VECTORS = json.loads((Path(__file__).parents[1] / "vectors" / "filter_vectors.json").read_text(encoding="utf-8"))


def make_filter(rules):
    flt = FileFilter.build(rules.get("include", ()), rules.get("exclude", ()), rules.get("extensions"),
                           rules.get("default_excludes", True), rules.get("gitignore"), rules.get("roots", ()))
    import dataclasses
    return dataclasses.replace(flt, exclude_parts=tuple(rules.get("exclude_parts", ())))


@pytest.mark.parametrize("case", VECTORS["normalize_path"], ids=lambda c: repr(c["in"]))
def test_normalize_path(case):
    if "error" in case:
        with pytest.raises(PathError, match=case["error"]):
            normalize_path(case["in"])
    else:
        assert normalize_path(case["in"]) == case["out"]


@pytest.mark.parametrize("case", VECTORS["normalize_text"], ids=lambda c: repr(c["in"]))
def test_normalize_text(case):
    assert normalize_text(case["in"]) == case["out"]


@pytest.mark.parametrize("case", VECTORS["check_path"], ids=lambda c: f"{c['path']}|{c.get('rules')}")
def test_check_path(case):
    d = make_filter(case.get("rules", {})).check_path(case["path"], case.get("size"))
    assert [d.outcome, d.reason] == case["expect"]


@pytest.mark.parametrize("case", VECTORS["check_content"], ids=lambda c: repr(c["text"][:30]))
def test_check_content(case):
    d = FileFilter.build().check_content("a.md", case["text"])
    assert [d.outcome, d.reason] == case["expect"]


def test_fixture_vault_gives_the_same_indexed_paths():
    from hippocampus.sync_core import scan_files

    vault = json.loads((Path(__file__).parents[1] / "vectors" / "vault_paths.json").read_text(encoding="utf-8"))
    scan = scan_files("v", [(p, t, None) for p, t in vault["files"].items()], FileFilter.build())
    assert sorted(d.path for d in scan.decisions if d.outcome == "indexed") == vault["indexed_by_default"]
    assert [d.path for d in scan.decisions if d.outcome == "rejected"] == vault["rejected_by_default"]
