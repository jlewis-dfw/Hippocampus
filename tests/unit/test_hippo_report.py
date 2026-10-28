"""tests/hippo_report.py: a module that skips or fails while pytest imports it must still show up in the run report."""
from __future__ import annotations

from types import SimpleNamespace

from hippo_report import Reporter, decide


def collect(report) -> dict:
    holder = SimpleNamespace(results={})
    Reporter.pytest_collectreport(holder, report)
    return holder.results


def report(nodeid: str, *, skipped: bool = False, failed: bool = False, longrepr=None, text: str = ""):
    return SimpleNamespace(nodeid=nodeid, skipped=skipped, failed=failed, longrepr=longrepr, longreprtext=text)


def test_a_module_skipped_at_import_is_recorded_as_one_skip() -> None:
    skip = report("tests/e2e/test_dashboard.py", skipped=True, longrepr=("f.py", 12, "Skipped: could not import 'playwright': No module named 'playwright'"))
    results = collect(skip)
    r = results["tests/e2e/test_dashboard.py"]
    assert (r.outcome, r.tier) == ("skipped", "e2e")
    assert r.message.startswith("could not import 'playwright'") and "whole module" in r.message


def test_a_collection_error_is_recorded_as_an_error_with_its_cause() -> None:
    failure = report("tests/e2e/test_x.py", failed=True, text="Traceback\nE   ModuleNotFoundError: No module named 'x'\n")
    r = collect(failure)["tests/e2e/test_x.py"]
    assert r.outcome == "error" and r.message == "collection error: ModuleNotFoundError: No module named 'x'"


def test_passing_reports_and_non_module_nodes_are_ignored() -> None:
    assert collect(report("tests/e2e/test_ok.py")) == {}
    assert collect(report("tests/e2e", skipped=True, longrepr="Skipped: x")) == {}
    assert collect(report("", failed=True)) == {}


def test_a_skipped_module_counts_as_a_gap_in_the_decision() -> None:
    results = list(collect(report("tests/e2e/test_dashboard.py", skipped=True, longrepr=("f.py", 1, "Skipped: could not import 'playwright'"))).values())
    decision = decide(results, None, 95.0, 80.0, gates=False)
    assert decision["verdict"] == "CONCERNS"
    assert any("skipped because something they need is missing" in g for g in decision["gaps"])
