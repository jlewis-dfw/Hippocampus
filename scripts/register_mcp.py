#!/usr/bin/env python3
"""Register the Hippocampus MCP server with Claude Code, using a token saved in token.env (it is never printed).

  scripts/register_mcp.py HIPPO_TOKEN_READER                  read-only, user scope, the service at HIPPO_URL or 127.0.0.1:8090
  scripts/register_mcp.py HIPPO_TOKEN_READER --dry-run        show the command (token hidden) and run nothing
  scripts/register_mcp.py HIPPO_TOKEN_WRITER --write          also allow the tool that indexes local files

The server is registered as "hippocampus" in the user scope, so the token lives in your own Claude configuration and never in a
project file. Project scope is refused: it would put the token in a file that is meant to be shared. Before registering, the token
is checked with GET /v1/whoami; an admin token is refused unless you pass --allow-admin (an assistant should have the least access
it needs). The server runs from this checkout's .venv.

Exit status: 0 registered (or dry run), 2 refused or could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from token_env import ROOT, env_path, read_vars


def whoami(url: str, token: str) -> dict:
    req = urllib.request.Request(url.rstrip("/") + "/v1/whoami", headers={"X-Hippo-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"the token was not accepted: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"cannot reach {url}: {e.reason}") from e


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("token_var", help="the variable in token.env that holds the token")
    ap.add_argument("--url", default=os.environ.get("HIPPO_URL", "http://127.0.0.1:8090"))
    ap.add_argument("--name", default="hippocampus")
    ap.add_argument("--scope", choices=["user", "local"], default="user")
    ap.add_argument("--write", action="store_true", help="leave out HIPPO_MCP_READONLY (adds the file-indexing tool)")
    ap.add_argument("--allow-admin", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    values = read_vars(env_path())
    if a.token_var not in values:
        print(f"{a.token_var} is not set in {env_path()} (known: {', '.join(values) or 'none'})", file=sys.stderr)
        return 2
    token = values[a.token_var]
    who = whoami(a.url, token)
    if who.get("admin") and not a.allow_admin:
        print(f"{who['name']} is an admin account: an assistant should use a read-only account "
              "(./hippocampus admin create-client --name NAME-reader --read SLUG). Pass --allow-admin to override.", file=sys.stderr)
        return 2
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"{python} does not exist: run scripts/prereqs.sh --dev --setup first", file=sys.stderr)
        return 2
    env = ["-e", f"HIPPO_URL={a.url}", "-e", f"HIPPO_TOKEN={token}"] + ([] if a.write else ["-e", "HIPPO_MCP_READONLY=1"])
    cmd = ["claude", "mcp", "add", a.name, "--scope", a.scope, *env, "--", str(python), "-m", "hippocampus.cli.mcp_server"]
    print("account    :", who["name"], "(read:", who.get("read"), "write:", who.get("write"), ")")
    print("command    :", " ".join(c.replace(token, "<token>") for c in cmd))
    if a.dry_run:
        return 0
    if shutil.which("claude") is None:
        print("the claude command is not on PATH: run the command above yourself", file=sys.stderr)
        return 2
    res = subprocess.run(cmd, capture_output=True, text=True)
    print((res.stdout + res.stderr).replace(token, "<token>").strip())
    return 0 if res.returncode == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
