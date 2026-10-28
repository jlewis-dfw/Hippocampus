#!/usr/bin/env python3
"""Keep Hippocampus tokens in the git-ignored token.env so they are never typed, printed or pasted.

  ./hippocampus up 2>&1 | scripts/token_env.py capture HIPPO_TOKEN_ADMIN       save the token a command prints, show the output without it
  ./hippocampus admin create-client --name bot-reader --read docs | scripts/token_env.py capture HIPPO_TOKEN_BOT_READER
  scripts/token_env.py list                                                      the variable names (never the values)
  scripts/token_env.py run HIPPO_TOKEN_ADMIN -- scripts/index_docs.py            run a command with that token in HIPPO_TOKEN

``capture`` reads standard input line by line, saves the first token it sees (``hip_`` and 43 characters) as NAME=token in
token.env, and passes every line through with the token replaced by ``hip_<saved as NAME>``. Anything that is not a token, an
account name or a grant passes through unchanged. The file is created with mode 600 and only when git ignores it. NAME is
upper-case letters, digits and underscores. Saving the same NAME again replaces the old value.

``run`` puts the token in the environment of one command (as HIPPO_TOKEN, plus HIPPO_URL if you set it) and never prints it.

Exit status: 0 done, 2 refused or could not run, 3 ``capture`` saw no token (the text still passes through).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_RE = re.compile(r"hip_[A-Za-z0-9_-]{20,}")
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def env_path() -> Path:
    return Path(os.environ.get("TOKEN_ENV_FILE") or ROOT / "token.env")


def read_vars(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            m = re.match(r"^([A-Z][A-Z0-9_]*)=(\S+)\s*$", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def git_ignores(path: Path) -> bool:
    """True when git ignores the file, or there is no git to ask (a copy of the tree without .git is not a repository)."""
    try:
        res = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=path.parent, capture_output=True)
    except FileNotFoundError:
        return True
    return res.returncode == 0 or res.returncode == 128  # 128: not a git repository


def save(path: Path, name: str, token: str) -> None:
    values = read_vars(path)
    values[name] = token
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("".join(f"{k}={v}\n" for k, v in values.items()))
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def capture(name: str) -> int:
    path = env_path()
    if not NAME_RE.match(name):
        print(f"invalid name {name!r}: use upper-case letters, digits and underscores", file=sys.stderr)
        return 2
    if not git_ignores(path):
        print(f"refusing to write {path}: git does not ignore it (add it to .gitignore first)", file=sys.stderr)
        return 2
    saved = False
    for line in sys.stdin:
        m = TOKEN_RE.search(line)
        if m and not saved:
            save(path, name, m.group(0))
            saved = True
        sys.stdout.write(TOKEN_RE.sub(f"hip_<saved as {name}>", line))
        sys.stdout.flush()
    if not saved:
        print(f"no token in the input: nothing was saved as {name}", file=sys.stderr)
        return 3
    print(f"[token saved to {path.name} as {name}, mode 600]", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="print the variable names, never the values")
    c = sub.add_parser("capture", help="save the token found on standard input")
    c.add_argument("name")
    r = sub.add_parser("run", help="run a command with the token in HIPPO_TOKEN")
    r.add_argument("name")
    r.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.cmd == "list":
        for name in read_vars(env_path()):
            print(name)
        return 0
    if a.cmd == "capture":
        return capture(a.name)
    values = read_vars(env_path())
    if a.name not in values:
        print(f"{a.name} is not set in {env_path()} (known: {', '.join(values) or 'none'})", file=sys.stderr)
        return 2
    argv = a.argv[1:] if a.argv[:1] == ["--"] else a.argv
    if not argv:
        print("nothing to run: scripts/token_env.py run NAME -- command ...", file=sys.stderr)
        return 2
    if argv[0].endswith(".py") and not os.access(argv[0], os.X_OK):
        argv = [sys.executable, *argv]
    os.execvpe(argv[0], argv, {**os.environ, "HIPPO_TOKEN": values[a.name]})


if __name__ == "__main__":
    sys.exit(main())
