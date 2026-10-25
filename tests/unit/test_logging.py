"""Operational log lines: one per request with an ID, failed logins, rejected files, jobs. Never a token or file content."""
import json
import logging

import pytest
from test_access import add_client, build

from hippocampus.service.jobs import JobRegistry
from hippocampus.service.observability import setup_logging

SECRET_TOKEN = "ghp_" + "a" * 36
BAD_TOKEN = "hip_" + "z" * 40
BODY = "UNIQUE-FILE-BODY-1234"


@pytest.fixture
def logs(caplog):
    caplog.set_level(logging.DEBUG, logger="hippocampus")
    return caplog


def test_a_request_logs_one_line_with_the_request_id(logs):
    c, _ = build()
    r = c.get("/v1/whoami?secret=queryvalue", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    line = [x for x in logs.records if "/v1/whoami" in x.getMessage()]
    assert len(line) == 1 and "200" in line[0].getMessage() and line[0].request_id == "abc-123"
    assert "queryvalue" not in logs.text


def test_an_unsafe_request_id_is_replaced():
    c, _ = build()
    rid = c.get("/v1/whoami", headers={"X-Request-ID": "bad id\twith space"}).headers["X-Request-ID"]
    assert rid != "bad id\twith space" and len(rid) == 16


def test_a_failed_login_is_logged_without_the_token(logs):
    c, mem = build()
    add_client(mem, "kobold", "hip_kobold")
    assert c.get("/v1/whoami", headers={"Authorization": f"Bearer {BAD_TOKEN}"}).status_code == 401
    assert "authentication failed" in logs.text and "/v1/whoami" in logs.text
    assert BAD_TOKEN not in logs.text and "z" * 20 not in logs.text


def test_a_rejected_file_logs_path_and_rule_never_content(logs):
    c, _ = build()
    c.post("/v1/collections", json={"slug": "notes", "name": "Notes"})
    r = c.post("/v1/ingest/files", json={"collection": "notes", "source": "vault", "final": True,
                                         "files": [{"path": "c.md", "content": f"{BODY} {SECRET_TOKEN}"}]})
    assert r.status_code == 200
    assert "file rejected: c.md (rule github-token)" in logs.text
    assert SECRET_TOKEN not in logs.text and BODY not in logs.text


def test_job_start_and_end_are_logged_and_an_error_logs_only_its_type(logs):
    jobs = JobRegistry()
    jobs.submit("crawl", lambda job: {"ok": 1}, background=False)

    def boom(job):
        raise ValueError(f"leaky detail {BODY}")

    jobs.submit("repo", boom, background=False)
    assert "started: crawl" in logs.text and "crawl done" in logs.text
    assert "repo error" in logs.text and "(ValueError)" in logs.text
    assert BODY not in logs.text


def test_json_format_and_level_come_from_the_environment(capsys):
    setup_logging({"HIPPO_LOG_FORMAT": "json", "HIPPO_LOG_LEVEL": "WARNING"})
    log = logging.getLogger("hippocampus")
    log.info("hidden")
    log.warning("shown", extra={"client": "10.0.0.1"})
    out = capsys.readouterr().err.strip().splitlines()
    assert len(out) == 1
    assert json.loads(out[0])["message"] == "shown" and json.loads(out[0])["client"] == "10.0.0.1"
    log.handlers.clear()
    log.propagate = True
    log.setLevel(logging.NOTSET)
