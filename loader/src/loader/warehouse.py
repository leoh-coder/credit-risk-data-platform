"""Postgres `raw` (Bronze) layer, written through DuckDB's postgres extension.

One reference_date = one Postgres transaction:
  delete that date in every raw table -> insert it again from the lake -> reconcile row counts with
  the manifest -> confirm the manifest did not change while we read -> record the load -> COMMIT.
Any failure rolls the whole date back, so readers see either the previous load or the new one.

Raw tables mirror the lake 1:1 and are created from the Parquet schema on first load (the Pandera
contract already versions that schema). INSERT ... BY NAME makes schema drift fail loudly.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime

import duckdb

from loader.lake import CommittedBatch, Lake
from loader.sql import quote_literal, safe_identifier

AUDIT_DDL = (
    """
    CREATE TABLE IF NOT EXISTS audit.lake_loads (
        load_id          uuid        PRIMARY KEY,
        reference_date   date        NOT NULL,
        batch_id         text        NOT NULL,
        manifest_sha256  char(64)    NOT NULL,
        contract_version text        NOT NULL,
        rows_loaded      jsonb       NOT NULL,
        started_at       timestamp   NOT NULL,
        finished_at      timestamp   NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS lake_loads_reference_date_idx "
    "ON audit.lake_loads (reference_date, finished_at)",
    "COMMENT ON TABLE audit.lake_loads IS 'One row per committed load of a lake reference_date into raw'",
    # Idempotent migration: tables created before this column existed get it on the next run.
    "ALTER TABLE audit.lake_loads ADD COLUMN IF NOT EXISTS manifest_entities jsonb",
    "COMMENT ON COLUMN audit.lake_loads.manifest_entities IS "
    "'Per-entity counts from the lake manifest (received/valid/quarantined); "
    "NULL for loads before 2026-10-16'",
)


class LoadError(Exception):
    """The date was rolled back and stays pending for the next run."""


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)  # naive UTC, project-wide convention


class Warehouse:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con

    def ensure_audit_table(self) -> None:
        for statement in AUDIT_DDL:
            self.con.execute(f"CALL postgres_execute('pg', {quote_literal(statement)})")
        self.con.execute("CALL pg_clear_cache()")  # let DuckDB see a table it did not create itself

    def ensure_raw_indexes(self, entities) -> None:
        """Index raw.<entity>(_reference_date): every load filters on it (DELETE, reconciliation).

        Without it each date is a sequential scan of an ever-growing table: quadratic over a backfill.
        Runs outside the per-date transaction (postgres_execute uses its own connection) and is
        idempotent, so it covers new tables and tables created by an older loader alike.
        """
        for entity in entities:
            name = safe_identifier(entity)
            if self._table_exists(name):
                ddl = f"CREATE INDEX IF NOT EXISTS {name}__reference_date_idx ON raw.{name} (_reference_date)"
                self.con.execute(f"CALL postgres_execute('pg', {quote_literal(ddl)})")

    def raw_tables(self) -> list[str]:
        rows = self.con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = 'pg' AND schema_name = 'raw'"
        ).fetchall()
        return [row[0] for row in rows]

    def loaded_versions(self) -> dict[date, str]:
        """reference_date -> manifest_sha256 of its latest successful load."""
        rows = self.con.execute(
            "SELECT reference_date, arg_max(manifest_sha256, finished_at) FROM pg.audit.lake_loads GROUP BY 1"
        ).fetchall()
        return dict(rows)

    def load(self, lake: Lake, batch: CommittedBatch) -> dict[str, int]:
        load_id, started_at = str(uuid.uuid4()), _utcnow()
        self.con.execute("BEGIN")
        try:
            rows = {
                entity: self._replace_partition(
                    lake, entity, batch.reference_date, stats["rows_valid"], load_id
                )
                for entity, stats in batch.entities.items()
            }
            current = lake.read_manifest(batch.reference_date)
            if current is None or current.manifest_sha256 != batch.manifest_sha256:
                raise LoadError(f"{batch.reference_date}: manifest changed while loading (ingestion rerun?)")
            self._record_load(load_id, batch, rows, started_at)
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        return rows

    def _table_exists(self, entity: str) -> bool:
        return bool(
            self.con.execute(
                "SELECT count(*) FROM duckdb_tables() "
                "WHERE database_name = 'pg' AND schema_name = 'raw' AND table_name = ?",
                [entity],
            ).fetchone()[0]
        )

    def _replace_partition(self, lake: Lake, entity: str, day: date, expected: int, load_id: str) -> int:
        table = f"pg.raw.{safe_identifier(entity)}"
        # hive_partitioning=false: reference_date already travels as the _reference_date lineage column.
        source = f"read_parquet({quote_literal(lake.partition_glob(entity, day))}, hive_partitioning = false)"
        lineage = "?::VARCHAR AS _load_id, ?::TIMESTAMP AS _loaded_at"

        if expected > 0 and not self._table_exists(entity):
            self.con.execute(
                f"CREATE TABLE {table} AS SELECT *, NULL::VARCHAR AS _load_id, NULL::TIMESTAMP AS _loaded_at "
                f"FROM {source} LIMIT 0"
            )
        if not self._table_exists(entity):
            return 0  # nothing ever landed for this entity, and nothing to land now

        self.con.execute(f"DELETE FROM {table} WHERE _reference_date = ?", [day])
        if expected > 0:  # the writer leaves no file for an empty partition
            self.con.execute(
                f"INSERT INTO {table} BY NAME SELECT *, {lineage} FROM {source}", [load_id, _utcnow()]
            )

        loaded = self.con.execute(
            f"SELECT count(*) FROM {table} WHERE _reference_date = ?", [day]
        ).fetchone()[0]
        if loaded != expected:
            raise LoadError(f"{day} {entity}: loaded {loaded} rows but the manifest committed {expected}")
        return loaded

    def _record_load(
        self, load_id: str, batch: CommittedBatch, rows: dict[str, int], started_at: datetime
    ) -> None:
        self.con.execute(
            "INSERT INTO pg.audit.lake_loads (load_id, reference_date, batch_id, manifest_sha256, "
            "contract_version, rows_loaded, manifest_entities, started_at, finished_at) "
            "VALUES (?::UUID, ?, ?, ?, ?, ?::JSON, ?::JSON, ?, ?)",
            [
                load_id,
                batch.reference_date,
                batch.batch_id,
                batch.manifest_sha256,
                batch.manifest["contract_version"],
                json.dumps(rows),
                json.dumps(batch.entities),
                started_at,
                _utcnow(),
            ],
        )
