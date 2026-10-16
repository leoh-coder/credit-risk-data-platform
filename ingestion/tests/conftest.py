from datetime import date

import pytest

from ingestion.config import Settings
from ingestion.generator import generate_batch

REFERENCE_DATE = date(2025, 6, 1)
AS_OF = date(2026, 10, 15)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        storage_backend="local",
        lake_local_path=str(tmp_path),
        customers_per_batch=60,
        dirty_rate=0.03,
    )


@pytest.fixture
def clean_batch():
    return generate_batch(REFERENCE_DATE, AS_OF, seed=7, customers_per_batch=60)
