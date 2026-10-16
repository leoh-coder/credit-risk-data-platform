"""Data Lake writer: Parquet files on S3-compatible storage (or local disk).

Layout (Hive-style partitions, readable by DuckDB, Spark, Athena, Trino...):

    s3://<bucket>/raw/<entity>/reference_date=YYYY-MM-DD/part-0.parquet
    s3://<bucket>/quarantine/<entity>/reference_date=YYYY-MM-DD/part-0.parquet
    s3://<bucket>/_manifests/reference_date=YYYY-MM-DD/manifest.json

Idempotency: each run *replaces* its partition (delete + write), so reprocessing
a date never duplicates data. The manifest is deleted FIRST and written LAST, and
acts as the commit marker: downstream loaders only read partitions that have a manifest.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

from ingestion.config import Settings


class LakeWriter:
    def __init__(self, settings: Settings):
        self.bucket = settings.lake_bucket
        if settings.storage_backend == "s3":
            kwargs = {
                "access_key": settings.aws_access_key_id or None,
                "secret_key": settings.aws_secret_access_key or None,
                "region": settings.aws_region,
            }
            local_s3 = bool(settings.s3_endpoint_url)
            if local_s3:  # S3-compatible server (SeaweedFS) instead of AWS
                url = urlparse(settings.s3_endpoint_url)
                kwargs |= {
                    "endpoint_override": url.netloc,
                    "scheme": url.scheme or "http",
                    "allow_bucket_creation": True,
                }
            self.fs: fs.FileSystem = fs.S3FileSystem(**kwargs)
            self.root = self.bucket
            if local_s3:
                # Locally the app bootstraps its own bucket. On real AWS the bucket is
                # provisioned by infrastructure-as-code (Terraform), never by the app.
                self.fs.create_dir(self.bucket)
        else:
            self.fs = fs.LocalFileSystem()
            self.root = str(Path(settings.lake_local_path).resolve() / self.bucket)

    def _partition(self, zone: str, entity: str, reference_date: date) -> str:
        return f"{self.root}/{zone}/{entity}/reference_date={reference_date.isoformat()}"

    def write_partition(self, zone: str, entity: str, reference_date: date, df: pd.DataFrame) -> str | None:
        partition = self._partition(zone, entity, reference_date)
        self.fs.delete_dir_contents(partition, missing_dir_ok=True)  # overwrite = idempotent rerun
        if df.empty:
            return None
        self.fs.create_dir(partition, recursive=True)
        path = f"{partition}/part-0.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, path, filesystem=self.fs, compression="zstd")
        return path

    def _manifest_dir(self, reference_date: date) -> str:
        return f"{self.root}/_manifests/reference_date={reference_date.isoformat()}"

    def invalidate_manifest(self, reference_date: date) -> None:
        """Un-commit a date before rewriting it, so readers never pair an old manifest with new files."""
        self.fs.delete_dir_contents(self._manifest_dir(reference_date), missing_dir_ok=True)

    def write_manifest(self, reference_date: date, manifest: dict) -> str:
        directory = self._manifest_dir(reference_date)
        self.fs.create_dir(directory, recursive=True)
        path = f"{directory}/manifest.json"
        with self.fs.open_output_stream(path) as out:
            out.write(json.dumps(manifest, indent=2, default=str).encode())
        return path
