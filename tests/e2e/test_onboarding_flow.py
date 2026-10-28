"""The new-user path end to end: /onboard and /crawl, and the scripts they stand on.

Three layers, cheapest first:

* always (needs only the stack): the token, indexing, MCP-registration and probe scripts against the real throwaway stack;
* HIPPO_RUN_NETWORK_TESTS=1: crawl_site.py crawls https://requests.readthedocs.io/en/latest/ into the stack (public pages, robots.txt allows it);
* HIPPO_RUN_ONBOARDING_E2E=1: scripts/eval_onboarding.py drives the real /onboard and /crawl commands in headless Claude Code, in a
  fresh checkout with its own stack, and every check it scores must pass. It costs money (see its --budget) and takes 20 to 40 minutes.

The probe endpoint is new: a stack started from an older image answers 404 for it, and those tests skip with the reason.
"""
import os
import shutil
import subprocess
import sys
import time

import pytest
from e2e_support import ROOT

SCRIPTS = ROOT / "scripts"
needs_venv = pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "python").exists(), reason="register_mcp points at this checkout's .venv")


@pytest.fixture(scope="module")
def tokens_file(tmp_path_factory):
    return tmp_path_factory.mktemp("tokens") / "token.env"


@pytest.fixture(scope="module")
def run(stack, tokens_file):
    def _run(script, *args, stdin=None, timeout=600):
        env = {**os.environ, "TOKEN_ENV_FILE": str(tokens_file), "HIPPO_URL": stack.url}
        return subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=stdin, capture_output=True, text=True, env=env,
                              timeout=timeout)
    return _run


@pytest.fixture(scope="module")
def saved_admin(stack, run):
    """The first-admin path of /onboard: the admin tool prints a token, token_env.py saves it and hides it."""
    out = stack.admin("create-client", "--name", "onb-admin", "--admin")
    res = run("token_env.py", "capture", "HIPPO_TOKEN_ADMIN", stdin=out)
    assert res.returncode == 0, res.stderr
    token = out.split("store it in the vault): ")[1].strip()
    assert token not in res.stdout + res.stderr
    yield token
    stack.admin("revoke-client", "onb-admin")  # the stack is shared: a second admin would break the last-admin tests


@pytest.fixture(scope="module")
def probe_present(stack, saved_admin):
    import httpx
    r = httpx.post(f"{stack.url}/v1/crawl/probe", json={"url": "http://127.0.0.1:9/"}, headers={"X-Hippo-Token": saved_admin})
    return r.status_code != 404


def test_the_saved_admin_token_verifies_and_is_never_in_the_output(stack, run, saved_admin, tokens_file):
    assert oct(tokens_file.stat().st_mode & 0o777) == "0o600"
    res = run("verify_setup.py", "--token-var", "HIPPO_TOKEN_ADMIN")
    assert res.returncode == 0, res.stdout
    assert "PASS  token accepted: onb-admin admin=True" in res.stdout
    assert saved_admin not in res.stdout + res.stderr
    assert run("token_env.py", "list").stdout.split() == ["HIPPO_TOKEN_ADMIN"]


def test_index_docs_through_token_run_then_a_reader_finds_it(stack, run, saved_admin, tmp_path):
    (tmp_path / "backups.md").write_text("# Backups\n\nRun pg_dump every night and copy the dump to another machine.\n")
    (tmp_path / "cats.md").write_text("# Cats\n\nCats purr, sleep most of the day and chase mice.\n")
    res = run("token_env.py", "run", "HIPPO_TOKEN_ADMIN", "--", str(SCRIPTS / "index_docs.py"), "--dir", str(tmp_path),
              "--collection", "onb-docs", "--prefix", "notes/")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "2 added" in res.stdout
    reader = stack.admin("create-client", "--name", "onb-reader", "--read", "onb-docs")
    assert run("token_env.py", "capture", "HIPPO_TOKEN_READER", stdin=reader).returncode == 0
    found = run("verify_setup.py", "--token-var", "HIPPO_TOKEN_READER", "--collection", "onb-docs", "--query", "how do I back up the database?")
    assert found.returncode == 0, found.stdout
    assert "notes/backups.md" in found.stdout


@needs_venv
def test_mcp_registration_is_read_only_and_refuses_an_admin(stack, run, saved_admin):
    run("token_env.py", "capture", "HIPPO_TOKEN_READER2", stdin=stack.admin("create-client", "--name", "onb-reader2", "--read", "onb-docs"))
    ok = run("register_mcp.py", "HIPPO_TOKEN_READER2", "--dry-run")
    assert ok.returncode == 0, ok.stderr
    assert "HIPPO_MCP_READONLY=1" in ok.stdout and "<token>" in ok.stdout and "hip_" not in ok.stdout
    refused = run("register_mcp.py", "HIPPO_TOKEN_ADMIN", "--dry-run")
    assert refused.returncode == 2 and "admin account" in refused.stderr


def test_a_reader_cannot_crawl_and_the_probe_refuses_a_private_address(stack, run, saved_admin, probe_present):
    if not probe_present:
        pytest.skip("this stack's image predates POST /v1/crawl/probe: rebuild with HIPPO_E2E_BUILD=1")
    res = run("crawl_site.py", "http://127.0.0.1:9/", "--collection", "onb-docs", "--token-var", "HIPPO_TOKEN_READER", "--plan")
    assert res.returncode == 2 and "HTTP 422" in res.stderr and "non-public" in res.stderr  # the address guard, before any request
    no_token = subprocess.run([sys.executable, str(SCRIPTS / "crawl_site.py"), "https://example.com/", "--collection", "onb-docs", "--plan"],
                              input="", capture_output=True, text=True, env={**os.environ, "HIPPO_URL": stack.url, "HIPPO_TOKEN": ""},
                              timeout=60)
    assert no_token.returncode == 2  # anonymous: refused (403), not a crash


def stack_resolves(stack, host: str, tries: int = 3) -> bool:
    """Can the API container itself resolve ``host``? (A container-network DNS fault is the machine's, not the product's.)"""
    code = f"import socket; socket.getaddrinfo('{host}', 443)"
    for _ in range(tries):
        if stack.compose("exec", "-T", "api", "python", "-c", code).returncode == 0:
            return True
        time.sleep(5)
    return False


SITE = "https://requests.readthedocs.io/en/latest/"


@pytest.mark.skipif(os.environ.get("HIPPO_RUN_NETWORK_TESTS") != "1", reason="set HIPPO_RUN_NETWORK_TESTS=1 to crawl requests.readthedocs.io (public docs)")
def test_crawl_the_requests_docs_with_the_script(stack, run, saved_admin, probe_present):
    if not probe_present:
        pytest.skip("this stack's image predates POST /v1/crawl/probe: rebuild with HIPPO_E2E_BUILD=1")
    if not stack_resolves(stack, "requests.readthedocs.io"):
        pytest.skip("the API container cannot resolve requests.readthedocs.io (container DNS on this machine, see troubleshooting.md)")
    plan = run("crawl_site.py", SITE, "--collection", "requests-docs", "--token-var", "HIPPO_TOKEN_ADMIN", "--plan")
    assert plan.returncode == 0, plan.stdout + plan.stderr
    assert "allowed" in plan.stdout and "pattern='/en/latest/'" in plan.stdout
    res = run("crawl_site.py", SITE, "--collection", "requests-docs", "--token-var", "HIPPO_TOKEN_ADMIN", "--yes",
              "--query", "how do I set a timeout on a request?", timeout=1800)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "crawl done:" in res.stdout
    hits = res.stdout.split("search:")[1]
    assert "quickstart" in hits or "advanced" in hits


@pytest.mark.skipif(os.environ.get("HIPPO_RUN_ONBOARDING_E2E") != "1",
                    reason="set HIPPO_RUN_ONBOARDING_E2E=1 to run /onboard and /crawl in headless Claude Code (costs money, 20-40 min)")
@pytest.mark.skipif(shutil.which("claude") is None, reason="the claude CLI is not installed")
def test_onboard_then_crawl_commands_get_a_new_user_going():
    sys.path.insert(0, str(SCRIPTS))
    import eval_onboarding

    res = eval_onboarding.run_eval(["onboard", "crawl"], ref=None, model=os.environ.get("HIPPO_EVAL_MODEL", "sonnet"),
                                   budget=float(os.environ.get("HIPPO_EVAL_BUDGET", "3")), total_budget=8.0, timeout=3000, keep=False)
    failed = [f"{c['id']}: {c['detail']}" for c in res["checks"] if not c["ok"]]
    listing = "\n  ".join(failed)
    assert not failed, f"failed checks (details in {res['dir']}):\n  {listing}"
