"""Logging setup and the request-log middleware.

``HIPPO_LOG_LEVEL`` (default INFO) and ``HIPPO_LOG_FORMAT`` (``text``, the default, or ``json``) are read once at start.
A log line never holds a token, a request or response body, a query string or a file's content: only method, path, status,
duration, a request ID, client address and rule names.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Mapping

from starlette.requests import Request

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_STD = set(vars(logging.makeLogRecord({})))

log = logging.getLogger("hippocampus")


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
               "logger": record.name, "message": record.getMessage()}
        out.update({k: v for k, v in vars(record).items() if k not in _STD and k != "message"})
        if record.exc_info:
            out["exception"] = record.exc_info[0].__name__ if record.exc_info[0] else ""  # type only: messages may hold data
        return json.dumps(out, default=str)


def setup_logging(env: Mapping[str, str]) -> None:
    """Configure the ``hippocampus`` logger from the environment. Safe to call twice."""
    level = getattr(logging, env.get("HIPPO_LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_ContextFilter())
    if env.get("HIPPO_LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
    log.handlers[:] = [handler]
    log.setLevel(level)
    log.propagate = False


def client_address(request: Request) -> str:
    return request.client.host if request.client else "-"


async def log_requests(request: Request, call_next):
    """One line per request with a request ID (an incoming ``X-Request-ID`` is kept when it is plain)."""
    incoming = request.headers.get("x-request-id", "")
    rid = incoming if _ID_RE.match(incoming) else uuid.uuid4().hex[:16]
    token = request_id.set(rid)
    started = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        log.info("%s %s -> %s in %.0f ms", request.method, request.url.path, status, (time.monotonic() - started) * 1000,
                 extra={"client": client_address(request)})
        request_id.reset(token)
