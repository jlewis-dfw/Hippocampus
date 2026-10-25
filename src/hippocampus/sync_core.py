"""The one place that decides which files are indexed (folder drop, saved sources, and the older repo ingesters).

Pure functions, no I/O, so the rules are identical on Linux, macOS and Windows and are tested with Windows-style and
NFD inputs. A decision always says why (``Decision.reason``) and names the file and the rule, never the content.

Paths are stored as ``/``-separated, NFC-normalised, relative paths. Text is hashed with LF line endings.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from .diff import Document

MAX_FILE_BYTES = 200_000
MAX_FILES = 5_000
MAX_PATH_CHARS = 1000

SECRET_RULES: tuple[tuple[str, re.Pattern], ...] = tuple(
    (name, re.compile(rx)) for name, rx in (
        ("aws-access-key-id", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"),
        ("aws-secret-access-key", r"(?i)aws.{0,20}secret.{0,20}[:=]\s*['\"]?[A-Za-z0-9/+=]{40}\b"),
        ("pem-private-key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        ("bearer-token", r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
        ("azure-storage-key", r"(?i)AccountKey=[A-Za-z0-9+/=]{40,}"),
        ("credential-assignment", r"""(?ix)\b(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret)\b
                                      ["']?\s*[:=]\s*["'][^"'\s$<{][^"'\s]{7,}["']"""),
    )
)

SECRET_PATH_RE = re.compile(
    r"(?i)(?:^|/)(?:\.env(?:\..*)?|\.vault_pass|vault\.yml|secrets?\.auto\.tfvars|id_(?:rsa|dsa|ecdsa|ed25519)"
    r"|[^/]*\.(?:pem|key|p12|pfx|tfstate(?:\.backup)?)|credentials(?:\.json)?)$"
)

SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

# Directory names that are never notes or code worth searching: version control, editor state, dependencies, build output,
# and the operating systems' trash and index folders.
DEFAULT_EXCLUDE_DIRS = (
    ".git", ".svn", ".hg", ".obsidian", ".trash", ".Trash*", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", ".Spotlight-V100", ".fseventsd", "$RECYCLE.BIN", "System Volume Information",
)
DEFAULT_EXCLUDE_FILES = (".DS_Store", "Thumbs.db", "desktop.ini", "~$*", ".~lock.*", "*.tmp", "*.swp")
DEFAULT_EXTENSIONS = (".md", ".markdown", ".txt", ".rst")

_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class PathError(ValueError):
    """The path cannot be stored; ``args[0]`` is a short rule name."""


def exclude_path_parts(default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Lower-case path fragments that are never indexed. ``HIPPO_EXCLUDE_PATH_PARTS`` (comma separated) replaces ``default``."""
    raw = os.environ.get("HIPPO_EXCLUDE_PATH_PARTS")
    if raw is None:
        return tuple(p.lower() for p in default)
    return tuple(p.strip().lower() for p in raw.split(",") if p.strip())


def normalize_path(raw: str) -> str:
    """A safe relative path with ``/`` separators and NFC names, or ``PathError``.

    Refuses absolute paths, drive letters, UNC paths, ``..``, control characters, Windows reserved names
    (``CON``, ``NUL.txt``, ...) and names ending in a dot or space. ``.`` segments and repeated separators are dropped.
    """
    if not isinstance(raw, str) or not raw:
        raise PathError("empty-path")
    if _CONTROL_RE.search(raw):
        raise PathError("control-character")
    path = unicodedata.normalize("NFC", raw.replace("\\", "/"))
    if path.startswith("/"):
        raise PathError("absolute-path")
    if _DRIVE_RE.match(path):
        raise PathError("drive-letter")
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if not parts:
        raise PathError("empty-path")
    for part in parts:
        if part == "..":
            raise PathError("path-traversal")
        if part != part.rstrip(". ") or part.split(".")[0].rstrip().lower() in _RESERVED:
            raise PathError("windows-reserved-name")
    out = "/".join(parts)
    if len(out) > MAX_PATH_CHARS:
        raise PathError("path-too-long")
    return out


def normalize_text(text: str) -> str:
    """LF line endings and no byte-order mark, so a Windows checkout and a Linux one hash the same."""
    return text.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")


def scan_secrets(text: str) -> list[str]:
    """Names of every secret rule that matches ``text`` (the matched text is never returned)."""
    return [name for name, rx in SECRET_RULES if rx.search(text)]


def file_uri(source: str, path: str) -> str:
    return f"files://{source}/{path}"


# --- glob rules (gitignore style: no "/" matches a name at any depth, a "/" anchors to the root, "dir/" is a folder) ---

def _glob_regex(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile("".join(out) + r"\Z")


@dataclass(frozen=True)
class Glob:
    pattern: str
    negate: bool = False
    dir_only: bool = False
    anchored: bool = False
    _rx: re.Pattern = field(default=None, repr=False, compare=False)

    @classmethod
    def parse(cls, line: str) -> Glob | None:
        line = line.strip().replace("\\", "/")
        if not line or line.startswith("#"):
            return None
        negate = line.startswith("!")
        line = line.removeprefix("!")
        dir_only = line.endswith("/")
        line = line.strip("/") if dir_only else line.rstrip("/")
        anchored = "/" in line
        line = line.lstrip("/")
        if not line:
            return None
        return cls(line, negate, dir_only, anchored, _glob_regex(line))

    def matches(self, path: str) -> bool:
        """True if ``path`` (a file) is the target or lies inside the target folder."""
        parts = path.split("/")
        for i in range(len(parts)):
            last = i == len(parts) - 1
            if self.dir_only and last:
                break
            if self.anchored:
                if self._rx.match("/".join(parts[: i + 1])):
                    return True
            elif self._rx.match(parts[i]):
                return True
        return False


def parse_globs(lines: Iterable[str]) -> tuple[Glob, ...]:
    return tuple(g for g in (Glob.parse(line) for line in lines) if g)


def _ignored(globs: tuple[Glob, ...], path: str) -> bool:
    """gitignore semantics: the last matching rule wins, and ``!rule`` re-includes."""
    hit = False
    for g in globs:
        if g.matches(path):
            hit = not g.negate
    return hit


_DEFAULT_DIR_GLOBS = parse_globs(DEFAULT_EXCLUDE_DIRS)
_DEFAULT_FILE_GLOBS = parse_globs(DEFAULT_EXCLUDE_FILES)


@dataclass(frozen=True)
class Decision:
    path: str
    outcome: str  # indexed | skipped | rejected
    reason: str | None = None
    warning: str | None = None

    def view(self) -> dict:
        return {k: v for k, v in (("path", self.path), ("outcome", self.outcome), ("reason", self.reason),
                                   ("warning", self.warning)) if v is not None}


@dataclass(frozen=True)
class FileFilter:
    """What to index. ``include`` empty means everything not excluded. ``extensions`` empty means any extension."""

    include: tuple[Glob, ...] = ()
    exclude: tuple[Glob, ...] = ()
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    default_excludes: bool = True
    gitignore: tuple[Glob, ...] = ()
    exclude_parts: tuple[str, ...] = field(default_factory=exclude_path_parts)

    @classmethod
    def build(cls, include: Iterable[str] = (), exclude: Iterable[str] = (), extensions: Iterable[str] | None = None,
              default_excludes: bool = True, gitignore: str | None = None) -> FileFilter:
        exts = DEFAULT_EXTENSIONS if extensions is None else tuple(
            "." + e.strip().lower().lstrip(".") for e in extensions if e.strip())
        return cls(parse_globs(include), parse_globs(exclude), exts, default_excludes,
                   parse_globs((gitignore or "").splitlines()))

    def check_path(self, raw: str, size: int | None = None) -> Decision:
        """Path-level decision (everything that needs no file content). ``size`` is in bytes when known."""
        try:
            path = normalize_path(raw)
        except PathError as exc:
            return Decision(str(raw)[:200], "rejected", f"invalid-path:{exc}")
        lower = path.lower()
        if any(p in lower for p in self.exclude_parts):
            return Decision(path, "rejected", "excluded-source")
        if SECRET_PATH_RE.search(path):
            return Decision(path, "rejected", "secret-file-name")
        if self.default_excludes:
            parts = path.split("/")
            if any(g._rx.match(p) for g in _DEFAULT_DIR_GLOBS for p in parts[:-1]) or any(
                    g._rx.match(parts[-1]) for g in _DEFAULT_FILE_GLOBS):
                return Decision(path, "skipped", "default-exclude")
        if self.extensions and os.path.splitext(lower)[1] not in self.extensions:
            return Decision(path, "skipped", "extension")
        if self.gitignore and _ignored(self.gitignore, path):
            return Decision(path, "skipped", "gitignored")
        if self.include and not any(g.matches(path) for g in self.include):
            return Decision(path, "skipped", "not-included")
        if self.exclude and any(g.matches(path) for g in self.exclude):
            return Decision(path, "skipped", "excluded-by-rule")
        if size is not None and size > MAX_FILE_BYTES:
            return Decision(path, "rejected", "too-large")
        return Decision(path, "indexed")

    def check_content(self, path: str, text: str) -> Decision:
        """Content-level decision for a file that passed ``check_path``."""
        if "\x00" in text:
            return Decision(path, "skipped", "binary")
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            return Decision(path, "rejected", "too-large")
        hits = scan_secrets(text)
        if hits:
            return Decision(path, "rejected", ", ".join(hits))
        if not text.strip():
            return Decision(path, "skipped", "empty")
        return Decision(path, "indexed")




def case_collisions(paths: Iterable[str]) -> list[list[str]]:
    """Groups of paths that differ only by case; they cannot coexist on macOS or Windows."""
    seen: dict[str, set[str]] = {}
    for p in paths:
        seen.setdefault(p.lower(), set()).add(p)
    return [sorted(v) for v in seen.values() if len(v) > 1]


@dataclass
class FileScan:
    documents: dict[str, Document] = field(default_factory=dict)  # uri -> Document, only files that passed
    decisions: list[Decision] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"indexed": 0, "skipped": 0, "rejected": 0}
        for d in self.decisions:
            out[d.outcome] += 1
        return out

    def rejected(self) -> list[dict]:
        return [{"path": d.path, "reasons": d.reason.split(", ")} for d in self.decisions if d.outcome == "rejected"]


def to_markdown(path: str, content: str) -> str:
    from .repo_ingest import _to_markdown

    return _to_markdown(path, content)


def scan_files(source: str, files: Iterable[tuple[str, str | None, int | None]], flt: FileFilter) -> FileScan:
    """Decide every ``(raw_path, text, size)``; text may be None for a path-only (preview) pass.

    Two inputs that normalise to one path, or differ only by case, keep the first and flag the rest.
    """
    if not SOURCE_NAME_RE.match(source):
        raise ValueError(f"invalid source name {source!r}")
    scan = FileScan()
    seen: dict[str, str] = {}
    for raw, text, size in files:
        d = flt.check_path(raw, size if size is not None else (len(text.encode("utf-8")) if text is not None else None))
        if d.outcome == "indexed" and text is not None:
            text = normalize_text(text)
            d = flt.check_content(d.path, text)
        if d.outcome == "indexed":
            prior = seen.get(d.path.lower())
            if prior == d.path:
                d = Decision(d.path, "skipped", "duplicate-path")
            elif prior is not None:
                d = Decision(d.path, "indexed", warning=f"case-collision with {prior}")
            seen.setdefault(d.path.lower(), d.path)
        scan.decisions.append(d)
        if d.outcome == "indexed" and text is not None:
            scan.documents[file_uri(source, d.path)] = Document(d.path, to_markdown(d.path, text))
    return scan
