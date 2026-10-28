"""scripts/token_env.py and scripts/register_mcp.py: tokens are saved with mode 600, never printed, and an admin token is not handed to an assistant."""
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "hip_" + "a1B2c3D4" * 5 + "xyz"  # 43 characters after the prefix


def token_env(tmp_path, *args, stdin="", env_file=None):
    env = {**os.environ, "TOKEN_ENV_FILE": str(env_file or tmp_path / "token.env")}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "token_env.py"), *args], input=stdin, capture_output=True,
                          text=True, env=env, cwd=tmp_path)


def git_repo(tmp_path, ignore):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("token.env\n" if ignore else "")


def test_capture_saves_the_token_hides_it_and_sets_mode_600(tmp_path):
    git_repo(tmp_path, ignore=True)
    res = token_env(tmp_path, "capture", "HIPPO_TOKEN_ADMIN", stdin=f"creating admin\ntoken (shown once): {TOKEN}\nbye\n")
    assert res.returncode == 0
    assert TOKEN not in res.stdout + res.stderr
    assert "hip_<saved as HIPPO_TOKEN_ADMIN>" in res.stdout and "creating admin" in res.stdout and "bye" in res.stdout
    path = tmp_path / "token.env"
    assert path.read_text() == f"HIPPO_TOKEN_ADMIN={TOKEN}\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_capture_twice_keeps_other_names_and_replaces_the_same_name(tmp_path):
    git_repo(tmp_path, ignore=True)
    other = "hip_" + "Z9y8X7w6" * 5 + "abc"
    token_env(tmp_path, "capture", "A_ONE", stdin=TOKEN + "\n")
    token_env(tmp_path, "capture", "B_TWO", stdin=other + "\n")
    token_env(tmp_path, "capture", "A_ONE", stdin=other + "\n")
    assert (tmp_path / "token.env").read_text() == f"A_ONE={other}\nB_TWO={other}\n"
    listed = token_env(tmp_path, "list")
    assert listed.stdout.split() == ["A_ONE", "B_TWO"] and other not in listed.stdout


def test_capture_without_a_token_passes_text_through_and_exits_3(tmp_path):
    git_repo(tmp_path, ignore=True)
    res = token_env(tmp_path, "capture", "X", stdin="an admin account already exists\n")
    assert res.returncode == 3 and "already exists" in res.stdout and not (tmp_path / "token.env").exists()


def test_capture_refuses_a_file_git_does_not_ignore_and_a_bad_name(tmp_path):
    git_repo(tmp_path, ignore=False)
    res = token_env(tmp_path, "capture", "OK_NAME", stdin=TOKEN + "\n")
    assert res.returncode == 2 and "does not ignore" in res.stderr and not (tmp_path / "token.env").exists()
    assert token_env(tmp_path, "capture", "lower-case", stdin=TOKEN + "\n").returncode == 2


def test_run_gives_the_command_the_token_without_printing_it(tmp_path):
    git_repo(tmp_path, ignore=True)
    token_env(tmp_path, "capture", "T_ONE", stdin=TOKEN + "\n")
    res = token_env(tmp_path, "run", "T_ONE", "--", sys.executable, "-c", "import os; print(len(os.environ['HIPPO_TOKEN']))")
    assert res.returncode == 0 and res.stdout.strip() == str(len(TOKEN)) and TOKEN not in res.stdout + res.stderr
    missing = token_env(tmp_path, "run", "NOPE", "--", "true")
    assert missing.returncode == 2 and "T_ONE" in missing.stderr


class Whoami(BaseHTTPRequestHandler):
    admin = False

    def do_GET(self):
        body = json.dumps({"name": "reader", "admin": self.admin, "read": ["docs"], "write": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Whoami)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def register(tmp_path, url, *args):
    env = {**os.environ, "TOKEN_ENV_FILE": str(tmp_path / "token.env")}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "register_mcp.py"), "READER", "--url", url, *args],
                          capture_output=True, text=True, env=env)


needs_venv = pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "python").exists(), reason="register_mcp points at this checkout's .venv")


@needs_venv
def test_register_dry_run_shows_a_read_only_command_without_the_token(tmp_path, server):
    (tmp_path / "token.env").write_text(f"READER={TOKEN}\n")
    Whoami.admin = False
    res = register(tmp_path, server, "--dry-run")
    assert res.returncode == 0 and TOKEN not in res.stdout + res.stderr
    assert "HIPPO_MCP_READONLY=1" in res.stdout and "--scope user" in res.stdout and "hippocampus.cli.mcp_server" in res.stdout
    assert "HIPPO_MCP_READONLY" not in register(tmp_path, server, "--dry-run", "--write").stdout


@needs_venv
def test_register_refuses_an_admin_token_project_scope_and_a_missing_variable(tmp_path, server):
    (tmp_path / "token.env").write_text(f"READER={TOKEN}\n")
    Whoami.admin = True
    refused = register(tmp_path, server, "--dry-run")
    assert refused.returncode == 2 and "admin account" in refused.stderr
    assert register(tmp_path, server, "--dry-run", "--allow-admin").returncode == 0
    assert register(tmp_path, server, "--scope", "project").returncode == 2  # argparse: not a choice
    (tmp_path / "token.env").write_text("")
    assert "not set" in register(tmp_path, server, "--dry-run").stderr


def test_scripts_are_executable():
    for name in ("token_env.py", "register_mcp.py", "verify_setup.py", "crawl_site.py"):
        assert shutil.which(str(ROOT / "scripts" / name)), name


def test_crawl_site_without_any_token_exits_cleanly_instead_of_crashing(tmp_path):
    env = {**os.environ, "TOKEN_ENV_FILE": str(tmp_path / "token.env"), "HIPPO_TOKEN": ""}
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "crawl_site.py"), "https://example.com/", "--collection", "docs"],
                         input="", capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode == 2 and "no token" in res.stderr and "Traceback" not in res.stderr


def test_crawl_site_uses_the_named_token_file_not_the_repository_one(tmp_path):
    env = {**os.environ, "TOKEN_ENV_FILE": str(tmp_path / "token.env"), "HIPPO_TOKEN": ""}
    res = subprocess.run([sys.executable, str(ROOT / "scripts" / "crawl_site.py"), "https://example.com/", "--collection", "docs",
                          "--token-var", "HIPPO_TOKEN_ADMIN"], capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode == 2 and str(tmp_path / "token.env") in res.stderr
