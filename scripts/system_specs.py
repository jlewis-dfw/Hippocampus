#!/usr/bin/env python3
"""Writes system-specs.md: what this machine offers Hippocampus (OS, memory, disk, Python, container engine, node, optional test tools).

  python scripts/system_specs.py          write system-specs.md in the repository root and print it
  python scripts/system_specs.py --print  print it only

The file is git-ignored and is refused if git would not ignore it. It holds versions and sizes only: no user name, host name, home-directory
path, environment variable or token. /onboard writes it, and /test reads it to choose the container engine. Plain Python: macOS, Linux, Windows.
The line "Container engine to use" is the first engine (docker, then podman) that has a working compose and a daemon that answers; set
CONTAINER_ENGINE to it when the default pick would be a stopped one.
"""
from __future__ import annotations

import datetime
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "system-specs.md"
ENGINES = ("docker", "podman")
MEMORY_FORMAT = {"docker": "{{.MemTotal}}", "podman": "{{.Host.MemTotal}}"}


def run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """(exit status, first line of the output); (127, '') when the program is missing or hangs."""
    try:
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    lines = (done.stdout or done.stderr).strip().splitlines()
    return done.returncode, lines[0].strip() if lines else ""


def gb(n: float) -> str:
    return f"{n / 1024**3:.1f} GB"


def total_memory() -> int | None:
    """Physical memory in bytes, or None when this OS does not say."""
    try:
        if sys.platform == "darwin":
            return int(run(["sysctl", "-n", "hw.memsize"])[1])
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong), ("page_avail", ctypes.c_ulonglong),
                            ("virt_total", ctypes.c_ulonglong), ("virt_avail", ctypes.c_ulonglong), ("ext", ctypes.c_ulonglong)]

            status = Status()
            status.length = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
            return int(status.total)
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def venv_python() -> str:
    for sub, name in (("bin", "python"), ("Scripts", "python.exe")):
        candidate = ROOT / ".venv" / sub / name
        if candidate.is_file():
            return str(candidate)
    return ""


def engine_report(name: str) -> tuple[list[str], bool]:
    """Markdown lines for one container engine, and whether it is usable (compose works and the daemon answers)."""
    if not shutil.which(name):
        return [f"- **{name}**: not installed"], False
    _, version = run([name, "--version"])
    compose_rc, compose = run([name, "compose", "version"])
    alive = run([name, "info"], timeout=30)[0] == 0
    memory = ""
    if alive:
        rc, raw = run([name, "info", "--format", MEMORY_FORMAT[name]])
        memory = f", engine memory {gb(int(raw))}" if rc == 0 and raw.isdigit() else ""
    state = "running" if alive else "installed but its daemon or machine is not running"
    lines = [f"- **{name}**: {version or 'version unknown'}; {state}{memory}",
             f"  - compose: {compose if compose_rc == 0 else 'not available'}"]
    return lines, alive and compose_rc == 0


def optional_tools(python: str) -> list[str]:
    if not python:
        return ["- no `.venv` yet (`scripts/prereqs.sh --dev --setup` creates it)"]
    probe = ("import importlib.util as u, os\n"
             "print('playwright', bool(u.find_spec('playwright')))\n"
             "print('fastembed', bool(u.find_spec('fastembed')))\n"
             "ok = False\n"
             "if u.find_spec('playwright'):\n"
             "    from playwright.sync_api import sync_playwright as s\n"
             "    p = s().start(); ok = os.path.exists(p.chromium.executable_path); p.stop()\n"
             "print('chromium', ok)\n")
    found: dict[str, str] = {}
    try:
        done = subprocess.run([python, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
        found = dict(line.split() for line in done.stdout.splitlines() if len(line.split()) == 2)
    except (OSError, subprocess.TimeoutExpired):
        pass

    def mark(key: str) -> str:
        return "installed" if found.get(key) == "True" else "not installed"

    return [f"- playwright (extra `browser`): {mark('playwright')}", f"- Chromium for playwright: {mark('chromium')}",
            f"- fastembed (extra `embed`, real-model test): {mark('fastembed')}"]


def build() -> str:
    venv = venv_python()
    reports = {name: engine_report(name) for name in ENGINES}
    usable = [name for name in ENGINES if reports[name][1]]
    node = run(["node", "--version"])[1] if shutil.which("node") else "not installed"
    memory = total_memory()
    lines = [
        "# System specs",
        "",
        f"Written by `scripts/system_specs.py` on {datetime.datetime.now(datetime.UTC):%Y-%m-%d %H:%M} UTC. Git-ignored. Versions and sizes only.",
        "",
        "## Machine",
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})",
        f"- CPUs: {os.cpu_count() or 'unknown'}; memory: {gb(memory) if memory else 'unknown'}; "
        f"free disk here: {gb(shutil.disk_usage(ROOT).free)}",
        f"- Python running this script: {platform.python_version()}; project `.venv`: "
        f"{run([venv, '--version'])[1] if venv else 'not created'}",
        f"- node: {node}",
        "",
        "## Container engines",
        *[line for name in ENGINES for line in reports[name][0]],
        "",
        f"**Container engine to use:** {usable[0] if usable else 'none (install Docker or Podman, and start it)'}",
    ]
    if usable and usable[0] != ENGINES[0] and shutil.which(ENGINES[0]):
        lines.append(f"- {ENGINES[0]} is installed but not usable, so set `CONTAINER_ENGINE={usable[0]}` where a script would otherwise pick {ENGINES[0]}.")
    lines += ["", "## Optional test tools", *optional_tools(venv), ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = build()
    if argv != ["--print"]:
        ignored = subprocess.run(["git", "check-ignore", "-q", OUT.name], cwd=ROOT, check=False).returncode == 0
        if not ignored:
            print(f"refusing to write {OUT.name}: git does not ignore it (add it to .gitignore first)", file=sys.stderr)
            return 2
        OUT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
