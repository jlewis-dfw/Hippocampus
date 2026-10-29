#!/usr/bin/env python3
"""Find documentation that no longer matches the repository: broken links, files, settings and API routes that do not exist.

  scripts/docs_check.py                 check README.md, CLAUDE.md, MEMORY.md, docs/ and .claude/commands/
  scripts/docs_check.py docs/User_Guide/ingesting.md .claude/commands/crawl.md

Checks, per file and line: (1) relative Markdown links that point at a missing file; (2) `backticked` repository paths (scripts/,
docs/, src/, db/, tests/, .claude/) that are missing; (3) HIPPO_* settings that no code, compose.yaml or hippocampus.env.example
mentions; (4) /v1/... routes that no router declares. Placeholders (<x>, {x}, *) match anything. It reports, it never edits.
Exit status: 0 nothing found, 1 findings, 2 could not run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATH_RE = re.compile(r"`((?:scripts|docs|src|db|tests|\.claude|deploy)/[^`\s*<>{}|]+)`")
LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
ENV_RE = re.compile(r"\bHIPPO_[A-Z0-9_]+\b")
ROUTE_RE = re.compile(r"(/v1/[A-Za-z0-9_\-/{}<>]*[A-Za-z0-9_}>])")
DECLARED_RE = re.compile(r"@router\.(?:get|post|put|patch|delete)\(\s*\"([^\"]+)\"")


def norm(route: str) -> str:
    return re.sub(r"[{<][^}>]*[}>]", "{}", route).rstrip("/")


def known_settings() -> set[str]:
    found: set[str] = set()
    sources = [*ROOT.glob("src/**/*.py"), *ROOT.glob("scripts/**/*.py"), *ROOT.glob("tests/**/*.py"), ROOT / "compose.yaml", ROOT / "hippocampus.env.example"]
    for f in sources:
        if f.is_file():
            found |= set(ENV_RE.findall(f.read_text(errors="replace")))
    return found


def declared_routes() -> set[str]:
    routes: set[str] = set()
    for f in (ROOT / "src/hippocampus/service").rglob("*.py"):
        routes |= {norm(m) for m in DECLARED_RE.findall(f.read_text(errors="replace"))}
    return routes


def main() -> int:
    targets = [ROOT / a for a in sys.argv[1:]] or [*(ROOT / n for n in ("README.md", "CLAUDE.md", "MEMORY.md")),
                                                    *ROOT.glob("docs/**/*.md"), *ROOT.glob(".claude/commands/*.md")]
    settings, routes = known_settings(), declared_routes()
    findings: list[str] = []
    for f in targets:
        if not f.is_file():
            print(f"{f} is not a file", file=sys.stderr)
            return 2
        rel = f.relative_to(ROOT).as_posix()
        in_fence = False
        for n, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
            for m in LINK_RE.findall(line):
                if not re.match(r"^(?:[a-z]+:|/)", m) and not (f.parent / m).resolve().exists():
                    findings.append(f"{rel}:{n}: broken link {m}")
            for m in PATH_RE.findall(line):
                if not (ROOT / re.split(r"#|::", m)[0].rstrip(".,:;)")).exists() and not re.search(r"NNNN|000N|\.local\.", m):
                    findings.append(f"{rel}:{n}: no such file {m}")
            for m in ENV_RE.findall(line):
                if m not in settings and not m.endswith("_"):
                    findings.append(f"{rel}:{n}: setting {m} is not used by any code, compose.yaml or hippocampus.env.example")
            for m in ROUTE_RE.findall(line):
                if norm(m) not in routes and not any(r.startswith(norm(m) + "/") for r in routes):
                    findings.append(f"{rel}:{n}: route {m} is not declared by any router")
    for item in findings:
        print(item)
    print(f"\n{len(targets)} file(s) checked, {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
