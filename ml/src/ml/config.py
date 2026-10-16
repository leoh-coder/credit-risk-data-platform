"""Runtime configuration (12-factor). The model reads the marts as `ml_reader`, a read-only login role
that only inherits `analytics_reader` (SELECT on marts)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "credit_risk_dw"
    ml_reader_user: str = "ml_reader"
    ml_reader_password: str = ""

    artifacts_dir: str = "./data/ml_artifacts"

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.ml_reader_user,
            password=self.ml_reader_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
