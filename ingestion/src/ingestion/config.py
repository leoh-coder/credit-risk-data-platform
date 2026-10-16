"""Runtime configuration, validated by Pydantic.

Every knob comes from environment variables (12-factor app). Pydantic validates
types and ranges at startup, so a typo in `.env` fails fast with a clear message
instead of producing a silently wrong batch three steps later.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Storage -------------------------------------------------------------
    storage_backend: Literal["s3", "local"] = "local"
    lake_bucket: str = "credit-risk-lake"
    lake_local_path: str = "./data/lake"

    # S3-compatible endpoint (SeaweedFS locally, empty string = real AWS S3)
    s3_endpoint_url: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"

    # --- Generator -----------------------------------------------------------
    seed: int = 42
    customers_per_batch: int = Field(default=150, ge=1, le=100_000)
    history_days: int = Field(default=90, ge=7, le=365)
    dirty_rate: float = Field(default=0.02, ge=0.0, le=0.5)

    @model_validator(mode="after")
    def _s3_needs_credentials(self) -> Settings:
        if self.storage_backend == "s3" and self.s3_endpoint_url and not self.aws_access_key_id:
            raise ValueError("STORAGE_BACKEND=s3 with a custom endpoint requires AWS_ACCESS_KEY_ID")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
