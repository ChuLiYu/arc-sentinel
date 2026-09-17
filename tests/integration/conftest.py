"""Integration fixtures: a throwaway Postgres 16 container + migrated schema (SPEC §10).

The container is created with the exact command from the task (``--rm``,
port 15432, user/pass/db all ``test``), migrated with ``alembic upgrade head``
against ``DATABASE_URL``, and forcibly removed on teardown.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_IMAGE = "postgres:16-alpine"
PG_NAME = "arc-sentinel-test-pg"
PG_PUBLIC_PORT = 15432
TEST_DATABASE_URL = f"postgresql+psycopg://test:test@localhost:{PG_PUBLIC_PORT}/test"


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _wait_for_postgres(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(
                host="localhost",
                port=PG_PUBLIC_PORT,
                dbname="test",
                user="test",
                password="test",
                connect_timeout=2,
            ) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(
        f"Postgres did not become ready on port {PG_PUBLIC_PORT} in {timeout:.0f}s: {last_error}"
    )


def _run_alembic_upgrade() -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="module")
def migrated_db() -> Generator[sessionmaker[Session], None, None]:
    """Start Postgres, migrate it, yield a bound sessionmaker, then clean up."""
    if shutil.which("docker") is None:
        pytest.skip("docker binary not available — integration test cannot run")
    _docker("rm", "-f", PG_NAME)  # clean slate even after a previous failed run
    started = _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        PG_NAME,
        "-p",
        f"{PG_PUBLIC_PORT}:5432",
        "-e",
        "POSTGRES_PASSWORD=test",
        "-e",
        "POSTGRES_USER=test",
        "-e",
        "POSTGRES_DB=test",
        PG_IMAGE,
    )
    if started.returncode != 0:
        raise RuntimeError(f"docker run failed: {started.stderr}")
    engine = None
    try:
        _wait_for_postgres()
        _run_alembic_upgrade()
        engine = create_engine(TEST_DATABASE_URL)
        factory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
        yield factory
    finally:
        if engine is not None:
            engine.dispose()
        _docker("rm", "-f", PG_NAME)
