"""CLI entry point.

    python -m loader.main run      # load every committed lake date that is new or changed

Incremental: a date is loaded when its manifest hash differs from the last one recorded in
audit.lake_loads. Each date commits or rolls back on its own; one bad date does not block the rest,
but makes the process exit non-zero so the orchestrator notices.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

import duckdb

from loader.config import Settings, get_settings
from loader.lake import Lake
from loader.sql import quote_literal
from loader.warehouse import LoadError, Warehouse

logger = logging.getLogger("loader")


@dataclass
class RunReport:
    loaded: list[date] = field(default_factory=list)
    up_to_date: list[date] = field(default_factory=list)
    failed: list[date] = field(default_factory=list)


def connect(settings: Settings) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    if settings.duckdb_extension_directory:
        con.execute(f"SET extension_directory = {quote_literal(settings.duckdb_extension_directory)}")
    extensions = ["postgres"] + (["httpfs"] if settings.storage_backend == "s3" else [])
    for extension in extensions:
        con.install_extension(extension)
        con.load_extension(extension)

    if settings.storage_backend == "s3":
        params = {
            "KEY_ID": settings.aws_access_key_id,
            "SECRET": settings.aws_secret_access_key,
            "REGION": settings.aws_region,
        }
        use_ssl = True
        if settings.s3_endpoint_url:  # S3-compatible server (SeaweedFS) instead of AWS
            endpoint = urlparse(settings.s3_endpoint_url)
            params |= {"ENDPOINT": endpoint.netloc, "URL_STYLE": "path"}
            use_ssl = endpoint.scheme == "https"
        options = ", ".join(f"{key} {quote_literal(value)}" for key, value in params.items())
        con.execute(f"CREATE SECRET lake (TYPE s3, {options}, USE_SSL {str(use_ssl).lower()})")

    con.execute(f"ATTACH {quote_literal(settings.postgres_dsn)} AS pg (TYPE postgres)")
    return con


def run(settings: Settings) -> RunReport:
    report = RunReport()
    con = connect(settings)
    try:
        lake, warehouse = Lake(con, settings.lake_root), Warehouse(con)
        warehouse.ensure_audit_table()
        loaded_versions = warehouse.loaded_versions()

        for day in lake.committed_dates():
            batch = lake.read_manifest(day)
            if batch is None:
                continue  # un-committed between listing and reading: an ingestion rerun started
            if loaded_versions.get(day) == batch.manifest_sha256:
                report.up_to_date.append(day)
                continue
            try:
                rows = warehouse.load(lake, batch)
            except (LoadError, duckdb.Error, ValueError, KeyError) as exc:
                logger.error("date=%s ROLLED BACK, stays pending: %s", day, exc)
                report.failed.append(day)
                continue
            report.loaded.append(day)
            summary = " ".join(f"{entity}={n}" for entity, n in rows.items())
            logger.info("date=%s batch=%s loaded: %s", day, batch.batch_id, summary)
    finally:
        con.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="loader", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="load every committed lake date that is new or changed")
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    settings = get_settings()
    logger.info(
        "loading %s into postgres %s/%s", settings.lake_root, settings.postgres_host, settings.postgres_db
    )
    report = run(settings)
    logger.info(
        "done: %d loaded, %d already up to date, %d failed",
        len(report.loaded),
        len(report.up_to_date),
        len(report.failed),
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
