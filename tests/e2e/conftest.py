"""End-to-end fixtures: a real Hippocampus stack (PostgreSQL + migrations + API + the real embedding model) started with
the project's own compose.yaml under a throwaway project name and port, so a running instance is never touched.

Needs Docker or Podman with compose. Without one every test here is skipped. Run on purpose:

    python -m pytest tests/e2e -v

The image hippocampus-api:local is reused when it exists (the first run builds it, a few minutes). Set HIPPO_E2E_BUILD=1
to rebuild it from the working tree first. CONTAINER_ENGINE=docker|podman overrides engine detection.
"""
from __future__ import annotations

import os
import re
import secrets
import uuid

import pytest
from e2e_support import ROOT, Api, Stack, find_engine, free_port


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    engine = find_engine()
    if engine is None:
        pytest.skip("neither 'docker compose' nor 'podman compose' is available")
    port = free_port()
    envfile = tmp_path_factory.mktemp("e2e") / "e2e.env"
    example = (ROOT / "hippocampus.env.example").read_text()
    text = re.sub(r"^POSTGRES_PASSWORD=.*$", f"POSTGRES_PASSWORD={secrets.token_hex(16)}", example, flags=re.M)
    text = re.sub(r"^HIPPO_PORT=.*$", f"HIPPO_PORT={port}", text, flags=re.M)
    envfile.write_text(text + "\nHIPPO_CRAWL_DELAY=0\nHIPPO_EXCLUDE_PATH_PARTS=private-infra\n")
    stk = Stack(engine, f"hippo-e2e-{uuid.uuid4().hex[:8]}", envfile, port)
    try:
        up = ["up", "-d"] + (["--build"] if os.environ.get("HIPPO_E2E_BUILD") == "1" else [])
        res = stk.compose(*up)
        if res.returncode != 0:
            raise RuntimeError(f"compose up failed:\n{res.stdout}\n{res.stderr}")
        stk.wait_ready()
        yield stk
    finally:
        stk.compose("down", "-v", "--remove-orphans")


@pytest.fixture(scope="session")
def anon(stack) -> Api:
    return Api(stack.url)


@pytest.fixture(scope="session")
def admin_token(stack) -> str:
    out = stack.admin("create-client", "--name", "e2e-root", "--admin")
    return out.split("store it in the vault): ")[1].strip()


@pytest.fixture(scope="session")
def admin(stack, admin_token) -> Api:
    return Api(stack.url, admin_token)
