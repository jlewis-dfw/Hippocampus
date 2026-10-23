"""The database for the integration tests: a throwaway container unless you point them at one yourself.

Without HIPPO_TEST_DSN a pgvector container is started for the session and removed afterwards (scripts/test_db.py), so these
tests run by default and never touch a database you use. With HIPPO_TEST_DSN set that database is used as it is, but only if its name
contains "test" or "dev" (set HIPPO_TEST_ALLOW_ANY_DB=1 to override), so a real database cannot be chosen by accident.
If there is no DSN and no container engine, the database tests are skipped.
"""
import importlib.util
import os
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "test_db.py"
DISPOSABLE = re.compile(r"test|dev", re.I)


def _database_name(dsn: str) -> str:
    from psycopg import conninfo

    return conninfo.conninfo_to_dict(dsn).get("dbname") or ""


@pytest.fixture(scope="session")
def database():
    """Makes HIPPO_TEST_DSN point at a disposable database for the whole session."""
    dsn = os.environ.get("HIPPO_TEST_DSN")
    if dsn:
        name = _database_name(dsn)
        if not DISPOSABLE.search(name) and os.environ.get("HIPPO_TEST_ALLOW_ANY_DB") != "1":
            pytest.fail(f"HIPPO_TEST_DSN points at database {name!r}, which does not look disposable (its name must contain "
                        "'test' or 'dev'). The tests create and delete rows. Unset it to use a throwaway container, "
                        "or set HIPPO_TEST_ALLOW_ANY_DB=1 if you are sure.", pytrace=False)
        yield dsn
        return
    pytest.importorskip("psycopg", reason="the database tests need the 'db' extra (psycopg)")
    spec = importlib.util.spec_from_file_location("hippo_test_db", SCRIPT)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if helper.find_engine() is None:
        pytest.skip("no HIPPO_TEST_DSN and neither docker nor podman is available for a throwaway database")
    try:
        engine, container, dsn = helper.start()
    except Exception as exc:
        pytest.fail(f"could not start the throwaway database: {exc}", pytrace=False)
    os.environ["HIPPO_TEST_DSN"] = dsn
    try:
        yield dsn
    finally:
        os.environ.pop("HIPPO_TEST_DSN", None)
        helper.stop(engine, container)
