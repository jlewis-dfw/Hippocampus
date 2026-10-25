#!/usr/bin/env python3
"""A throwaway pgvector database for the tests, in a container that is removed afterwards (needs the `db` extra).

  test_db.py up           start a container (random host port and password, bound to 127.0.0.1), apply the migrations,
                          print "<container> <dsn>" on one line
  test_db.py down NAME    remove that container

The integration tests and scripts/check.sh both use it, so no test ever needs to touch a database you care about.
Docker or Podman is used (CONTAINER_ENGINE=docker|podman overrides detection); the image is pgvector/pgvector:pg16.
Exit status: 0 ok, 1 failed, 2 no container engine.
"""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

IMAGE = "docker.io/pgvector/pgvector:pg16"
DB_NAME = "hippocampus_test"  # contains "test": the integration tests refuse databases whose name does not look disposable
READY_TIMEOUT = 90


def find_engine() -> str | None:
    wanted = os.environ.get("CONTAINER_ENGINE")
    for name in [wanted] if wanted else ["docker", "podman"]:
        if shutil.which(name) and subprocess.run([name, "version"], capture_output=True).returncode == 0:
            return name
    return None


def start() -> tuple[str, str, str]:
    """Returns (engine, container name, dsn) of a migrated, empty database."""
    import psycopg

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from hippocampus.storage.migrate import apply_all

    engine = find_engine()
    if engine is None:
        raise RuntimeError("neither docker nor podman is available")
    name, password = f"hippo-test-db-{secrets.token_hex(4)}", secrets.token_hex(12)
    run = subprocess.run(
        [engine, "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={password}", "-e", f"POSTGRES_DB={DB_NAME}",
         "-p", "127.0.0.1::5432", IMAGE],
        capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError(f"could not start the database container: {run.stderr.strip()}")
    try:
        mapped = subprocess.run([engine, "port", name, "5432/tcp"], capture_output=True, text=True).stdout.split()[0]
        dsn = f"postgresql://postgres:{password}@127.0.0.1:{mapped.rsplit(':', 1)[1]}/{DB_NAME}"
        deadline, streak = time.time() + READY_TIMEOUT, 0
        while streak < 2:  # the image restarts the server once after initialising, so require two answers in a row
            if time.time() > deadline:
                raise RuntimeError("the database did not become ready in time")
            try:
                with psycopg.connect(dsn, connect_timeout=2) as conn:
                    conn.execute("SELECT 1")
                streak += 1
            except psycopg.OperationalError:
                streak = 0
            time.sleep(1)
        with psycopg.connect(dsn, autocommit=True) as conn:
            apply_all(conn)
        return engine, name, dsn
    except BaseException:
        stop(engine, name)
        raise


def stop(engine: str, name: str) -> None:
    subprocess.run([engine, "rm", "-f", name], capture_output=True)


def main() -> int:
    args = sys.argv[1:]
    if args == ["up"]:
        if find_engine() is None:
            print("neither docker nor podman is available", file=sys.stderr)
            return 2
        try:
            _, name, dsn = start()
        except Exception as exc:
            print(exc, file=sys.stderr)
            return 1
        print(name, dsn)
        return 0
    if len(args) == 2 and args[0] == "down":
        engine = find_engine()
        if engine:
            stop(engine, args[1])
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
