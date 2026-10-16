"""Runtime configuration, validated by Pydantic.

Same 12-factor approach as the ingestion job: every knob is an environment variable, and the lake
variables have the same names in both jobs, so one `.env` / compose anchor configures both.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _libpq_value(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Lake (read side) ------------------------------------------------------
    storage_backend: Literal["s3", "local"] = "local"
    lake_bucket: str = "credit-risk-lake"
    lake_local_path: str = "./data/lake"
    s3_endpoint_url: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # --- Warehouse -------------------------------------------------------------
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "credit_risk_dw"
    postgres_user: str = ""
    postgres_password: str = ""

    # Empty = DuckDB's default (~/.duckdb/extensions). The Docker image bakes the extensions in.
    duckdb_extension_directory: str = ""

    @model_validator(mode="after")
    def _requires_credentials(self) -> Settings:
        if not self.postgres_user:
            raise ValueError("POSTGRES_USER is required")
        if self.storage_backend == "s3" and self.s3_endpoint_url and not self.aws_access_key_id:
            raise ValueError("STORAGE_BACKEND=s3 with a custom endpoint requires AWS_ACCESS_KEY_ID")
        return self

    @property
    def lake_root(self) -> str:
        if self.storage_backend == "s3":
            return f"s3://{self.lake_bucket}"
        return (Path(self.lake_local_path).resolve() / self.lake_bucket).as_posix()

    @property
    def postgres_dsn(self) -> str:
        """libpq key/value string; values are quoted so passwords with spaces or quotes still work."""
        parts = {
            "host": self.postgres_host,
            "port": str(self.postgres_port),
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
        }
        return " ".join(f"{key}={_libpq_value(value)}" for key, value in parts.items())


@lru_cache
def get_settings() -> Settings:
    return Settings()
