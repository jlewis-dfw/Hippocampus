"""The single-process limit: jobs and upload sessions live in memory, so the service must run as one worker."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping

log = logging.getLogger("hippocampus")

CONSEQUENCE = (
    "jobs and folder-upload sessions are kept in process memory, so with several workers a running upload fails "
    "with 404 and job history is split between processes"
)


def worker_count(env: Mapping[str, str]) -> int:
    """The configured worker count from ``WEB_CONCURRENCY`` (the variable uvicorn and gunicorn read); 1 when unset or invalid."""
    try:
        return max(1, int(env.get("WEB_CONCURRENCY", "1")))
    except ValueError:
        return 1


def check_single_worker(env: Mapping[str, str] | None = None) -> None:
    """Warn when more than one worker is configured; raise ``RuntimeError`` too when ``HIPPO_REQUIRE_SINGLE_WORKER`` is true."""
    env = os.environ if env is None else env
    n = worker_count(env)
    if n <= 1:
        return
    message = f"{n} workers configured (WEB_CONCURRENCY): {CONSEQUENCE}. Run one worker."
    if env.get("HIPPO_REQUIRE_SINGLE_WORKER", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(message)
    log.warning(message)
