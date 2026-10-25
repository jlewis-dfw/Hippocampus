import logging

import pytest

from hippocampus.workers import check_single_worker, worker_count


@pytest.mark.parametrize("env,n", [({}, 1), ({"WEB_CONCURRENCY": "1"}, 1), ({"WEB_CONCURRENCY": "4"}, 4),
                                   ({"WEB_CONCURRENCY": "x"}, 1), ({"WEB_CONCURRENCY": "0"}, 1)])
def test_worker_count(env, n):
    assert worker_count(env) == n


def test_one_worker_is_silent(caplog):
    with caplog.at_level(logging.WARNING, logger="hippocampus"):
        check_single_worker({"WEB_CONCURRENCY": "1"})
    assert caplog.records == []


def test_a_second_worker_logs_a_warning_naming_the_consequence(caplog):
    with caplog.at_level(logging.WARNING, logger="hippocampus"):
        check_single_worker({"WEB_CONCURRENCY": "2"})
    assert "2 workers" in caplog.text and "404" in caplog.text


def test_the_guard_can_refuse_to_start():
    with pytest.raises(RuntimeError, match="Run one worker"):
        check_single_worker({"WEB_CONCURRENCY": "2", "HIPPO_REQUIRE_SINGLE_WORKER": "true"})
    check_single_worker({"WEB_CONCURRENCY": "1", "HIPPO_REQUIRE_SINGLE_WORKER": "true"})
