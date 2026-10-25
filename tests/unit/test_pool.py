"""Pool settings, and the answer when the database pool is exhausted (the real pool is in tests/integration)."""
import logging
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from test_api import KeywordEmbedder

from hippocampus.api import create_app
from hippocampus.store import StoreBusy, pool_settings


def test_pool_defaults_are_small_and_the_environment_overrides_them():
    assert pool_settings({}) == (1, 5, 5.0)
    assert pool_settings({"HIPPO_DB_POOL_MIN": "2", "HIPPO_DB_POOL_MAX": "8", "HIPPO_DB_POOL_TIMEOUT": "0.5"}) == (2, 8, 0.5)


@pytest.mark.parametrize("env", [{"HIPPO_DB_POOL_MIN": "0"}, {"HIPPO_DB_POOL_MIN": "3", "HIPPO_DB_POOL_MAX": "2"},
                                 {"HIPPO_DB_POOL_TIMEOUT": "0"}, {"HIPPO_DB_POOL_MAX": "many"}])
def test_bad_pool_settings_fail_at_start_with_a_clear_message(env):
    with pytest.raises(ValueError, match="HIPPO_DB_POOL"):
        pool_settings(env)


def test_an_exhausted_pool_is_a_503_with_retry_after_and_a_log_line(caplog):
    @contextmanager
    def busy():
        raise StoreBusy("no database connection is free")
        yield  # pragma: no cover

    c = TestClient(create_app(busy, KeywordEmbedder()))
    with caplog.at_level(logging.WARNING, logger="hippocampus"):
        r = c.get("/v1/collections")
    assert r.status_code == 503 and r.headers["retry-after"] == "1"
    assert r.json() == {"detail": "the database is busy, try again shortly"}
    assert "database pool exhausted" in caplog.text
