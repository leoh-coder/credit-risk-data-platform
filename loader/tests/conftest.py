import json
import os
import shutil
import uuid
from datetime import date
from pathlib import Path

import duckdb
import psycopg
import pytest
from ingestion.config import Settings as IngestionSettings
from ingestion.main import run_batch
from ingestion.writer import LakeWriter
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict

from loader.config import Settings

DAY_1 = date(2026, 1, 1)
DAY_2 = date(2026, 1, 2)
AS_OF = date(2026, 10, 15)
BUCKET = "credit-risk-lake"

ROOT = Path(__file__).resolve().parents[2]
INIT_SQL = ROOT / "infra" / "postgres" / "init.sql"
# The compose Postgres publishes its port on this address only.
ADMIN_HOST = "127.0.0.1"


class AdminCredentials(BaseSettings):
    """The compose admin credentials: environment first, then the root .env; defaults match .env.example."""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    postgres_db: str = "credit_risk_dw"
    postgres_user: str = "dw_admin"
    postgres_password: str = "change_me_dw_password"


def admin_dsn(env_file: Path = ROOT / ".env") -> str:
    """LOADER_TEST_PG_DSN wins (CI sets it); otherwise the credentials of the compose Postgres."""
    explicit = os.environ.get("LOADER_TEST_PG_DSN")
    if explicit:
        return explicit
    credentials = AdminCredentials(_env_file=env_file)
    return make_conninfo(
        host=ADMIN_HOST,
        port=5432,
        dbname=credentials.postgres_db,
        user=credentials.postgres_user,
        password=credentials.postgres_password,
    )


def connection_skip_reason(exc: psycopg.OperationalError) -> str:
    if "password authentication failed" in str(exc):
        return (
            "Postgres refused the credentials: the tests use POSTGRES_USER / POSTGRES_PASSWORD from the "
            f"environment or the root .env, or LOADER_TEST_PG_DSN ({exc})"
        )
    return f"Postgres not reachable, start it with `docker compose up -d --wait` ({exc})"


ADMIN_DSN = admin_dsn()


@pytest.fixture(scope="session")
def pg_admin() -> dict:
    try:
        psycopg.connect(ADMIN_DSN, connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise  # never let CI go green by skipping the integration tests
        pytest.skip(connection_skip_reason(exc))
    return conninfo_to_dict(ADMIN_DSN)


@pytest.fixture
def warehouse_db(pg_admin):
    """A throwaway database per test, initialized with the same init.sql as the real warehouse."""
    name = f"loader_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    try:
        with psycopg.connect(make_conninfo(ADMIN_DSN, dbname=name), autocommit=True) as conn:
            conn.execute(INIT_SQL.read_text())
        yield name
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


@pytest.fixture
def settings(lake_path, warehouse_db, pg_admin) -> Settings:
    return Settings(
        _env_file=None,
        storage_backend="local",
        lake_local_path=str(lake_path),
        postgres_host=pg_admin["host"],
        postgres_port=int(pg_admin.get("port", 5432)),
        postgres_db=warehouse_db,
        postgres_user=pg_admin["user"],
        postgres_password=pg_admin.get("password", ""),
    )


def query(settings: Settings, sql: str, params=None) -> list[tuple]:
    with psycopg.connect(settings.postgres_dsn) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.fixture(scope="session")
def lake_template(tmp_path_factory) -> Path:
    """A real two-day lake written by the ingestion job, built once per session."""
    root = tmp_path_factory.mktemp("lake-template")
    settings = IngestionSettings(
        _env_file=None, storage_backend="local", lake_local_path=str(root), customers_per_batch=40
    )
    writer = LakeWriter(settings)
    for day in (DAY_1, DAY_2):
        run_batch(day, AS_OF, settings, writer)
    return root


@pytest.fixture
def lake_path(lake_template, tmp_path) -> Path:
    """Per-test copy, so tests can delete or tamper with manifests freely."""
    destination = tmp_path / "lake"
    shutil.copytree(lake_template, destination)
    return destination


def manifest_file(lake_path: Path, day: date) -> Path:
    return lake_path / BUCKET / "_manifests" / f"reference_date={day}" / "manifest.json"


def edit_manifest(lake_path: Path, day: date, change) -> None:
    path = manifest_file(lake_path, day)
    manifest = json.loads(path.read_text())
    change(manifest)
    path.write_text(json.dumps(manifest, indent=2))


class ManifestReadFails:
    """DuckDB connection whose read of one date's manifest raises `error`; every other query runs as usual."""

    def __init__(self, con, day: date, error: Exception):
        self._con, self._error = con, error
        self._manifest = f"reference_date={day.isoformat()}/manifest.json"

    def execute(self, query: str, *args):
        if "read_text" in query and self._manifest in query:
            raise self._error
        return self._con.execute(query, *args)


def http_error(status: int) -> duckdb.HTTPException:
    """What httpfs raises when S3 answers with an error status (a missing object is a 404)."""
    error = duckdb.HTTPException(f"HTTP {status}")
    error.status_code = status
    return error
