"""Helpers for the end-to-end tests: finding the container engine, starting a throwaway stack, a small HTTP client.

Kept out of conftest.py so test files can import them without clashing with other conftest files.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
START_TIMEOUT = 600  # seconds; the first start loads the embedding model


def find_engine() -> str | None:
    wanted = os.environ.get("CONTAINER_ENGINE")
    for name in ([wanted] if wanted else ["docker", "podman"]):
        if shutil.which(name) and subprocess.run([name, "compose", "version"], capture_output=True).returncode == 0:
            return name
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    def __init__(self, engine: str, project: str, envfile: Path, port: int):
        self.engine, self.project, self.envfile, self.port = engine, project, envfile, port
        self.url = f"http://127.0.0.1:{port}"

    def compose(self, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
        cmd = [self.engine, "compose", "-p", self.project, "-f", str(ROOT / "compose.yaml"),
               "--env-file", str(self.envfile), *args]
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)

    def wait_ready(self, timeout: int = START_TIMEOUT) -> None:
        deadline, last = time.time() + timeout, "no answer"
        while time.time() < deadline:
            try:
                if httpx.get(f"{self.url}/v1/status", timeout=3).status_code == 200:
                    return
                last = "status was not 200"
            except httpx.HTTPError as exc:
                last = repr(exc)
            time.sleep(3)
        logs = self.compose("logs", "--tail", "40").stdout
        raise RuntimeError(f"stack did not become ready ({last})\n{logs}")

    def admin(self, *args: str) -> str:
        res = self.compose("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "hippocampus.cli.admin", *args)
        if res.returncode != 0:
            raise RuntimeError(f"admin {args} failed: {res.stderr or res.stdout}")
        return res.stdout


class Api:
    """A tiny HTTP client; ``token`` None means an anonymous caller."""

    def __init__(self, url: str, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = httpx.Client(base_url=url, headers=headers, timeout=60)

    def __getattr__(self, name):  # get, post, patch, delete, ...
        return getattr(self.http, name)
