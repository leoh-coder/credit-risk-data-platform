"""Read-only view of the lake through DuckDB (local disk or S3).

A date is committed only when its manifest exists. The manifest's SHA-256 versions the commit, so an
ingestion rerun is detected even though its batch_id is deterministic.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date

import duckdb

from loader.sql import quote_literal

_MANIFEST_DATE = re.compile(r"reference_date=(\d{4}-\d{2}-\d{2})/manifest\.json$")


@dataclass(frozen=True)
class CommittedBatch:
    reference_date: date
    manifest: dict
    manifest_sha256: str

    @property
    def batch_id(self) -> str:
        return self.manifest["batch_id"]

    @property
    def entities(self) -> dict[str, dict]:
        return self.manifest["entities"]


class Lake:
    def __init__(self, con: duckdb.DuckDBPyConnection, root: str):
        self.con = con
        self.root = root.rstrip("/")

    def manifest_path(self, reference_date: date) -> str:
        return f"{self.root}/_manifests/reference_date={reference_date.isoformat()}/manifest.json"

    def partition_glob(self, entity: str, reference_date: date) -> str:
        return f"{self.root}/raw/{entity}/reference_date={reference_date.isoformat()}/*.parquet"

    def committed_dates(self) -> list[date]:
        pattern = quote_literal(f"{self.root}/_manifests/*/manifest.json")
        rows = self.con.execute(f"SELECT file FROM glob({pattern})").fetchall()
        matches = (_MANIFEST_DATE.search(file.replace("\\", "/")) for (file,) in rows)
        return sorted({date.fromisoformat(m.group(1)) for m in matches if m})

    def read_manifest(self, reference_date: date) -> CommittedBatch | None:
        """None when the date is not committed (never landed, or a rerun is in flight)."""
        path = quote_literal(self.manifest_path(reference_date))
        try:
            content = self.con.execute(f"SELECT content FROM read_text({path})").fetchone()
        except duckdb.IOException:
            return None
        if content is None:
            return None
        text = content[0]
        return CommittedBatch(reference_date, json.loads(text), hashlib.sha256(text.encode()).hexdigest())
