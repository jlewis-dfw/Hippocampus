"""Test run reports: one timestamped folder per run, laid out like the BMAD Test Architect reports.

    tests/reports/20261023103015/report.html   the report: decision, evidence and gaps, tiers, coverage, failures, skips
    tests/reports/20261023103015/results.json  the same facts for machines (the gate decision is `decision`)
    tests/reports/20261023103015/junit.xml     for CI viewers (pytest's own --junitxml)
    tests/reports/latest/                      a copy of the newest run's files, with this machine's paths masked (report.html and
                                               results.json are committed; junit.xml is git-ignored)

The gate decision follows the TEA vocabulary (PASS / CONCERNS / FAIL), derived from fixed rules (see ``decide``) that the
report prints. Coverage of src/hippocampus is measured when the ``coverage`` package is installed (the ``dev`` extra) and the run
is not end-to-end only. Registered by tests/conftest.py; no plugin needs installing.

Environment: HIPPO_REPORT=0 turns the report off, HIPPO_COVERAGE_TARGET (default 95) and HIPPO_COVERAGE_FLOOR (default 80) set
the percentages.
"""
from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import subprocess
import time
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPORTS = Path(__file__).resolve().parent / "reports"
SOURCE = ROOT / "src" / "hippocampus"
TIERS = ("unit", "integration", "e2e")
OPT_IN_SKIP = re.compile(r"\bset [A-Z][A-Z0-9_]+=")  # "...; set HIPPO_RUN_MODEL_TESTS=1": skipped on purpose, not a gap


@dataclass
class Result:
    nodeid: str
    tier: str
    outcome: str = "passed"  # passed | failed | error | skipped | xfailed | xpassed
    duration: float = 0.0
    message: str = ""  # first line of the failure, or the skip reason
    detail: str = ""  # the full failure text


@dataclass
class ModuleCoverage:
    path: str
    statements: int
    missed: int
    missing: str

    @property
    def percent(self) -> float:
        return 100.0 if not self.statements else 100.0 * (self.statements - self.missed) / self.statements


@dataclass
class Coverage:
    modules: list[ModuleCoverage] = field(default_factory=list)

    @property
    def statements(self) -> int:
        return sum(m.statements for m in self.modules)

    @property
    def missed(self) -> int:
        return sum(m.missed for m in self.modules)

    @property
    def percent(self) -> float:
        return 100.0 if not self.statements else 100.0 * (self.statements - self.missed) / self.statements


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def tier_of(nodeid: str) -> str:
    parts = nodeid.replace("\\", "/").split("/")
    return next((p for p in parts if p in TIERS), "other")


def ranges(numbers: list[int]) -> str:
    """[1, 2, 3, 7, 9, 10] -> '1-3, 7, 9-10'"""
    out, i = [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        out.append(str(numbers[i]) if i == j else f"{numbers[i]}-{numbers[j]}")
        i = j + 1
    return ", ".join(out)


def decide(results: list[Result], coverage: Coverage | None, target: float, floor: float, gates: bool = True) -> dict:
    """The gate. FAIL: a test failed or errored, nothing ran, or coverage is under the floor. CONCERNS: all green but coverage is
    under the target, or a test was skipped for a reason that is not an opt-in switch. PASS: otherwise. Coverage only counts
    (``gates``) when whole test tiers ran; a run of a few files is judged on its tests alone and shows coverage for information."""
    count = lambda *o: sum(1 for r in results if r.outcome in o)  # noqa: E731
    failed, errors = count("failed", "xpassed"), count("error")
    skipped = [r for r in results if r.outcome == "skipped"]
    unplanned = [r for r in skipped if not OPT_IN_SKIP.search(r.message)]
    evidence = [f"{len(results)} tests: {count('passed', 'xfailed')} passed, {failed} failed, {errors} errors, {len(skipped)} skipped"]
    gaps: list[str] = []
    if coverage is not None:
        note = f"(target {target:g}%, floor {floor:g}%)" if gates else "(informational: only part of the suite ran)"
        evidence.append(f"line coverage of src/hippocampus: {coverage.percent:.1f}% {note}")
    if not gates:
        coverage = None
    if not results:
        gaps.append("no tests ran")
    if failed or errors:
        gaps.append(f"{failed + errors} test(s) failed or errored")
    if coverage is not None and coverage.percent < floor:
        gaps.append(f"coverage {coverage.percent:.1f}% is under the floor of {floor:g}%")
    verdict = "FAIL" if gaps else "PASS"
    concerns: list[str] = []
    if verdict == "PASS":
        if coverage is not None and coverage.percent < target:
            concerns.append(f"coverage {coverage.percent:.1f}% is under the target of {target:g}%")
        if unplanned:
            concerns.append(f"{len(unplanned)} test(s) were skipped because something they need is missing (see Skipped)")
        if concerns:
            verdict = "CONCERNS"
    return {"verdict": verdict, "evidence": evidence, "gaps": gaps + concerns}


def git_info() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return ""

    return {"branch": run("rev-parse", "--abbrev-ref", "HEAD"), "commit": run("rev-parse", "--short", "HEAD"),
            "dirty": bool(run("status", "--porcelain"))}


# --------------------------------------------------------------------------- the pytest side

class Reporter:
    def __init__(self, config):
        self.config = config
        self.results: dict[str, Result] = {}
        self.cov = None
        self.started = time.time()
        self.run_id = datetime.now().strftime("%Y%m%d%H%M%S")
        self.dir = REPORTS / self.run_id
        args = [str(a).replace("\\", "/") for a in config.args]
        names = [tier_of(a) for a in args] if args else ["unit", "integration"]
        self.whole_tiers = not args or all(a.rstrip("/").split("/")[-1] in (*TIERS, "tests") for a in args)  # not a few files
        self.e2e_only = bool(names) and set(names) == {"e2e"}
        self.scope = " + ".join(dict.fromkeys(names)) if all(n in TIERS for n in names) else " ".join(args)

    def configure(self) -> None:
        n = 1
        while self.dir.exists():  # two runs in the same second never share a folder
            n += 1
            self.dir = REPORTS / f"{self.run_id}-{n}"
        if not self.config.option.collectonly:
            self.dir.mkdir(parents=True)
            if not self.config.option.xmlpath:
                self.config.option.xmlpath = str(self.dir / "junit.xml")

    def pytest_sessionstart(self) -> None:
        self.started = time.time()
        if self.e2e_only or os.environ.get("HIPPO_COVERAGE", "1") == "0" or self.config.pluginmanager.hasplugin("_cov"):
            return
        try:
            import coverage

            self.cov = coverage.Coverage(source=[str(SOURCE)], data_file=None)
            self.cov.start()
        except Exception:
            self.cov = None

    def pytest_runtest_logreport(self, report) -> None:
        if report.when == "call" or (report.when == "setup" and report.outcome != "passed") or \
                (report.when == "teardown" and report.outcome == "failed"):
            r = self.results.setdefault(report.nodeid, Result(report.nodeid, tier_of(report.nodeid)))
            r.duration += report.duration
            if report.when == "teardown":
                r.outcome, r.message = "error", r.message or "error during teardown"
                r.detail += "\n" + (report.longreprtext or "")
            elif hasattr(report, "wasxfail"):
                r.outcome = "xfailed" if report.skipped else "xpassed"
            elif report.passed:
                r.outcome = "passed"
            elif report.skipped:
                r.outcome = "skipped"
                lr = report.longrepr
                r.message = (lr[2] if isinstance(lr, tuple) else str(lr)).removeprefix("Skipped: ")
            else:
                r.outcome = "error" if report.when == "setup" else "failed"
                text = report.longreprtext or ""
                r.detail = text
                errs = [ln[1:].strip() for ln in text.splitlines() if ln.startswith("E ")]
                r.message = errs[0] if errs else (text.strip().splitlines() or [""])[-1]

    def pytest_collectreport(self, report) -> None:
        """A module that skips or fails while it is being imported (``pytest.importorskip`` at its top, a missing import) never reaches
        ``pytest_runtest_logreport``, so none of its tests would appear anywhere. Record the module itself as one result, so the skip
        or the error shows up in the totals and the decision. The number of tests it hides is not known: the message says so."""
        if not report.nodeid.endswith(".py") or not (report.skipped or report.failed):
            return
        r = self.results.setdefault(report.nodeid, Result(report.nodeid, tier_of(report.nodeid)))
        if report.skipped:
            lr = report.longrepr
            reason = (lr[2] if isinstance(lr, tuple) else str(lr)).removeprefix("Skipped: ")
            r.outcome, r.message = "skipped", f"{reason} (the whole module was skipped; its tests are not counted)"
        else:
            text = report.longreprtext or ""
            errs = [ln[1:].strip() for ln in text.splitlines() if ln.startswith("E ")]
            r.outcome, r.detail = "error", text
            r.message = f"collection error: {errs[0] if errs else (text.strip().splitlines() or [''])[-1]}"

    def collect_coverage(self) -> Coverage | None:
        if self.cov is None:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return self._analyse()

    def _analyse(self) -> Coverage:
        self.cov.stop()
        cov = Coverage()
        for path in sorted(SOURCE.rglob("*.py")):
            try:
                _, statements, _, missing, _ = self.cov.analysis2(str(path))
            except Exception:
                continue
            if path.name == "__init__.py" and not statements:
                continue
            cov.modules.append(ModuleCoverage(str(path.relative_to(ROOT)), len(statements), len(missing), ranges(sorted(missing))))
        return cov

    @pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self, terminalreporter, exitstatus) -> None:
        path = self.finish(exitstatus)
        if path is not None:
            terminalreporter.write_line(f"Test report: {path.as_uri()}")

    def finish(self, exitstatus: int) -> Path | None:
        if hasattr(self.config, "workerinput") or self.config.option.collectonly or not self.dir.exists():
            return None
        coverage = self.collect_coverage()
        results = sorted(self.results.values(), key=lambda r: r.nodeid)
        target, floor = _env_number("HIPPO_COVERAGE_TARGET", 95), _env_number("HIPPO_COVERAGE_FLOOR", 80)
        decision = decide(results, coverage, target, floor, gates=self.whole_tiers)
        meta = {
            "run_id": self.run_id, "scope": self.scope, "started": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
            "duration_seconds": round(time.time() - self.started, 2), "python": platform.python_version(),
            "platform": platform.platform(), "command": "pytest " + " ".join(map(str, self.config.invocation_params.args)),
            "git": git_info(), "exit_status": int(exitstatus), "target": target, "floor": floor,
        }
        (self.dir / "report.html").write_text(render(meta, decision, results, coverage), encoding="utf-8")
        (self.dir / "results.json").write_text(json.dumps({
            **meta, "decision": decision, "tiers": tier_table(results),
            "coverage": None if coverage is None else {
                "percent": round(coverage.percent, 2), "statements": coverage.statements, "missed": coverage.missed,
                "modules": [asdict(m) | {"percent": round(m.percent, 2)} for m in coverage.modules]},
            "tests": [asdict(r) | {"detail": r.detail[:4000]} for r in results]}, indent=2), encoding="utf-8")
        try:
            publish_latest(self.dir)
        except OSError:  # the timestamped folder is still there
            pass
        return self.dir / "report.html"


def publish_latest(run_dir: Path, reports: Path = REPORTS) -> Path:
    """Make ``reports/latest`` a real folder holding a copy of this run's files, so it can be committed (a link would dangle in a clone and
    needs special rights on Windows). The copy replaces this machine's repository and home paths by ``<repo>`` and ``~``: tracebacks hold
    them, and the repository is public. The run's own folder keeps the originals."""
    latest = reports / "latest"
    if latest.is_symlink() or latest.is_file():
        latest.unlink()
    elif latest.is_dir():
        shutil.rmtree(latest)
    latest.mkdir()
    swaps = []
    for private, mask in ((ROOT, "<repo>"), (Path.home(), "~")):  # the longer path first: the repository sits inside the home folder
        for form in {str(private), private.as_posix(), json.dumps(str(private))[1:-1]}:
            swaps.append((form, mask))
    for name in ("report.html", "results.json", "junit.xml"):
        if (run_dir / name).is_file():
            text = (run_dir / name).read_text(encoding="utf-8")
            for form, mask in swaps:
                text = text.replace(form, mask)
            (latest / name).write_text(text, encoding="utf-8")
    return latest


def _count(rs: list[Result], *outcomes: str) -> int:
    return sum(1 for r in rs if r.outcome in outcomes)


def tier_table(results: list[Result]) -> dict:
    out = {}
    for tier in [t for t in (*TIERS, "other") if any(r.tier == t for r in results)]:
        rs = [r for r in results if r.tier == tier]
        out[tier] = {"total": len(rs), "passed": _count(rs, "passed", "xfailed"), "failed": _count(rs, "failed", "xpassed"),
                     "errors": _count(rs, "error"), "skipped": _count(rs, "skipped"),
                     "duration": round(sum(r.duration for r in rs), 2)}
    return out


# --------------------------------------------------------------------------- the HTML

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2230;--mute:#667085;--line:#e3e6ec;--pass:#117a45;--passbg:#e3f6ec;--warn:#9a6700;--warnbg:#fff3d1;
--fail:#b42318;--failbg:#fde7e5;--skip:#475467;--skipbg:#eceff3;--bar:#2f6fed;--code:#f1f3f7}
@media (prefers-color-scheme:dark){:root{--bg:#10131a;--card:#171b24;--ink:#e6e9f0;--mute:#9aa3b5;--line:#2a3140;--pass:#5fd49a;--passbg:#12301f;
--warn:#f2c25b;--warnbg:#3a2e0e;--fail:#ff8a80;--failbg:#3d1714;--skip:#b6bfd0;--skipbg:#232a37;--bar:#6b9bff;--code:#0e1118}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px}
.meta{color:var(--mute);font-size:13px}.meta span{margin-right:14px;display:inline-block}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.banner{border-radius:10px;padding:16px 20px;margin:18px 0;border:1px solid}.banner h2{margin:0 0 6px;font-size:22px}
.banner.PASS{background:var(--passbg);color:var(--pass);border-color:var(--pass)}.banner.CONCERNS{background:var(--warnbg);color:var(--warn);border-color:var(--warn)}
.banner.FAIL{background:var(--failbg);color:var(--fail);border-color:var(--fail)}.banner ul{margin:6px 0 0 18px;padding:0;color:var(--ink)}
.banner b.k{display:block;margin-top:8px;color:inherit}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px}.cards .card{padding:12px 14px}
.cards .n{font-size:26px;font-weight:650;line-height:1.1}.cards .l{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:600;font-size:12px;
text-transform:uppercase;letter-spacing:.04em;background:var(--bg)}tr:last-child td{border-bottom:0}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:8px;background:var(--line);border-radius:4px;min-width:90px;overflow:hidden}.bar i{display:block;height:100%;background:var(--bar)}
.bar.ok i{background:var(--pass)}.bar.low i{background:var(--warn)}.bar.bad i{background:var(--fail)}
.tag{display:inline-block;font-size:11px;font-weight:650;letter-spacing:.04em;padding:1px 7px;border-radius:999px;text-transform:uppercase}
.tag.passed,.tag.xfailed{background:var(--passbg);color:var(--pass)}.tag.failed,.tag.error,.tag.xpassed{background:var(--failbg);color:var(--fail)}
.tag.skipped{background:var(--skipbg);color:var(--skip)}.tag.optin{background:var(--skipbg);color:var(--skip)}.tag.gap{background:var(--warnbg);color:var(--warn)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}summary{cursor:pointer;padding:10px 14px;font-weight:600}
details>div{padding:0 14px 12px}pre{background:var(--code);border-radius:8px;padding:10px 12px;overflow:auto;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;margin:8px 0 0}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}.mute{color:var(--mute)}.f{margin:12px 0}.f h3{font-size:15px;margin:0 0 2px}
.how{font-size:13px;color:var(--mute)}.how li{margin:2px 0}
.scroll{overflow-x:auto;border-radius:10px}
@media (max-width:640px){th.hide,td.hide{display:none}th,td{padding:8px 8px}}
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _dur(seconds: float) -> str:
    return f"{seconds * 1000:.0f} ms" if seconds < 1 else f"{seconds:.1f} s" if seconds < 60 else f"{int(seconds // 60)} min {int(seconds % 60)} s"


def _bar(percent: float, target: float, floor: float) -> str:
    cls = "ok" if percent >= target else "low" if percent >= floor else "bad"
    return f'<div class="bar {cls}" role="img" aria-label="{percent:.1f} percent"><i style="width:{percent:.1f}%"></i></div>'


def render(meta: dict, decision: dict, results: list[Result], coverage: Coverage | None) -> str:
    n = lambda *o: sum(1 for r in results if r.outcome in o)  # noqa: E731
    total, passed, failed, errors, skipped = len(results), n("passed", "xfailed"), n("failed", "xpassed"), n("error"), n("skipped")
    ran = passed + failed + errors
    rate = 100.0 * passed / ran if ran else 0.0
    g = meta["git"]
    where = f'{_e(g["branch"])} @ {_e(g["commit"])}{" (uncommitted changes)" if g["dirty"] else ""}' if g["commit"] else "not a git checkout"
    verdict = decision["verdict"]
    sentence = {"PASS": "Every test passed and the evidence meets the thresholds.",
                "CONCERNS": "Nothing failed, but the evidence has gaps that need a decision.",
                "FAIL": "Do not release on this run."}[verdict]
    li = lambda items: "".join(f"<li>{_e(i)}</li>" for i in items)  # noqa: E731
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hippocampus test report {_e(meta['run_id'])}</title><style>{CSS}</style></head><body><main>
<h1>Hippocampus test report</h1>
<div class="meta"><span>Run {_e(meta['run_id'])}</span><span>Scope: {_e(meta['scope'])}</span><span>{_e(meta['started'].replace('T', ' '))}</span>
<span>{_dur(meta['duration_seconds'])}</span><span>{where}</span><span>Python {_e(meta['python'])}</span></div>
<section class="banner {verdict}" aria-label="Gate decision"><h2>Gate decision: {verdict}</h2><div>{_e(sentence)}</div>
<b class="k">Evidence</b><ul>{li(decision['evidence'])}</ul>
{'<b class="k">Gaps and concerns</b><ul>' + li(decision['gaps']) + '</ul>' if decision['gaps'] else ''}</section>
<div class="cards">
<div class="card"><div class="n">{total}</div><div class="l">Tests</div></div>
<div class="card"><div class="n" style="color:var(--pass)">{passed}</div><div class="l">Passed</div></div>
<div class="card"><div class="n" style="color:var(--fail)">{failed}</div><div class="l">Failed</div></div>
<div class="card"><div class="n" style="color:var(--fail)">{errors}</div><div class="l">Errors</div></div>
<div class="card"><div class="n">{skipped}</div><div class="l">Skipped</div></div>
<div class="card"><div class="n">{rate:.1f}%</div><div class="l">Pass rate (of tests run)</div></div></div>"""]

    out.append('<h2>Results by tier</h2><table><thead><tr><th>Tier</th><th class="n">Tests</th><th class="n">Passed</th><th class="n">Failed</th>'
               '<th class="n">Errors</th><th class="n">Skipped</th><th class="n hide">Time</th><th class="hide">Pass rate</th></tr></thead><tbody>')
    for tier, row in tier_table(results).items():
        ran = row["passed"] + row["failed"] + row["errors"]
        pr = 100.0 * row["passed"] / ran if ran else 100.0
        out.append(f'<tr><td>{_e(tier)}</td><td class="n">{row["total"]}</td><td class="n">{row["passed"]}</td><td class="n">{row["failed"]}</td>'
                   f'<td class="n">{row["errors"]}</td><td class="n">{row["skipped"]}</td><td class="n hide">{_dur(row["duration"])}</td>'
                   f'<td class="hide">{_bar(pr, 100, 90)}</td></tr>')
    out.append("</tbody></table>")

    bad = [r for r in results if r.outcome in ("failed", "error", "xpassed")]
    out.append(f"<h2>Failures and errors ({len(bad)})</h2>")
    if not bad:
        out.append('<div class="card mute">None.</div>')
    for r in bad:
        out.append(f'<details open><summary><span class="tag {r.outcome}">{r.outcome}</span> <span class="mono">{_e(r.nodeid)}</span></summary><div>'
                   f'<div class="f"><h3>{_e(r.message or "no message")}</h3></div><pre>{_e(r.detail or "no details")}</pre></div></details>')

    skips = [r for r in results if r.outcome == "skipped"]
    out.append(f"<h2>Skipped ({len(skips)})</h2>")
    if not skips:
        out.append('<div class="card mute">None.</div>')
    else:
        out.append('<table><thead><tr><th>Test</th><th>Reason</th><th>Kind</th></tr></thead><tbody>')
        for r in skips:
            opt = bool(OPT_IN_SKIP.search(r.message))
            out.append(f'<tr><td class="mono">{_e(r.nodeid)}</td><td>{_e(r.message)}</td><td><span class="tag {"optin" if opt else "gap"}">'
                       f'{"opt-in" if opt else "missing need"}</span></td></tr>')
        out.append("</tbody></table>")

    out.append("<h2>Coverage of src/hippocampus</h2>")
    if coverage is None:
        out.append('<div class="card mute">Not measured for this run (end-to-end runs exercise a container, not this process; '
                   "or the <code>coverage</code> package is not installed: <code>pip install -e \".[dev]\"</code>).</div>")
    else:
        out.append(f'<div class="card"><b>{coverage.percent:.1f}%</b> of {coverage.statements} statements ({coverage.missed} missed). '
                   f'Target {meta["target"]:g}%, floor {meta["floor"]:g}%.{_bar(coverage.percent, meta["target"], meta["floor"])}</div>'
                   '<table style="margin-top:10px"><thead><tr><th>Module</th><th class="n">Statements</th><th class="n">Missed</th>'
                   '<th class="n">Covered</th><th class="hide">&nbsp;</th><th>Missing lines</th></tr></thead><tbody>')
        for m in sorted(coverage.modules, key=lambda m: (m.percent, m.path)):
            out.append(f'<tr><td class="mono">{_e(m.path.removeprefix("src/hippocampus/"))}</td><td class="n">{m.statements}</td><td class="n">{m.missed}</td>'
                       f'<td class="n">{m.percent:.1f}%</td><td class="hide">{_bar(m.percent, meta["target"], meta["floor"])}</td>'
                       f'<td class="mono mute">{_e(m.missing) or "-"}</td></tr>')
        out.append("</tbody></table>")

    out.append("<h2>All results by file</h2>")
    files: dict[str, list[Result]] = {}
    for r in results:
        files.setdefault(r.nodeid.split("::")[0], []).append(r)
    for path, rs in files.items():
        flagged = any(r.outcome in ("failed", "error", "xpassed") for r in rs)
        counts = f'{sum(1 for r in rs if r.outcome in ("passed", "xfailed"))}/{len(rs)} passed'
        out.append(f'<details{" open" if flagged else ""}><summary><span class="mono">{_e(path)}</span> <span class="mute">{counts}</span></summary><div>'
                   '<table><tbody>')
        for r in rs:
            out.append(f'<tr><td><span class="tag {r.outcome}">{r.outcome}</span></td><td class="mono">{_e(r.nodeid.split("::", 1)[-1])}</td>'
                       f'<td class="n mute">{_dur(r.duration)}</td></tr>')
        out.append("</tbody></table></div></details>")

    slow = sorted(results, key=lambda r: -r.duration)[:10]
    out.append("<h2>Slowest tests</h2><table><thead><tr><th>Test</th><th class=\"n\">Time</th></tr></thead><tbody>")
    for r in slow:
        out.append(f'<tr><td class="mono">{_e(r.nodeid)}</td><td class="n">{_dur(r.duration)}</td></tr>')
    out.append("</tbody></table>")

    out.append(f"""<h2>How the decision is made</h2><div class="card how"><ul>
<li><b>FAIL</b>: any test failed or errored, no test ran, or coverage is under the floor ({meta['floor']:g}%).</li>
<li><b>CONCERNS</b>: everything passed, but coverage is under the target ({meta['target']:g}%), or a test was skipped because something it needs is missing.
Skips that name an opt-in switch (&ldquo;set NAME=1&rdquo;) do not count.</li>
<li><b>PASS</b>: none of the above.</li></ul>
<div>Reproduce: <span class="mono">{_e(meta['command'])}</span> &middot; Files: <span class="mono">results.json</span>, <span class="mono">junit.xml</span></div></div>
</main></body></html>""")
    return "".join(out).replace("<table", '<div class="scroll"><table').replace("</table>", "</table></div>")
