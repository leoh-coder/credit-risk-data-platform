import hashlib

import duckdb
import pytest

from loader.lake import Lake
from loader.sql import quote_literal, safe_identifier
from tests.conftest import BUCKET, DAY_1, DAY_2, manifest_file


@pytest.fixture
def lake(lake_path):
    con = duckdb.connect()
    yield Lake(con, (lake_path / BUCKET).as_posix())
    con.close()


def test_committed_dates_are_the_dates_with_a_manifest(lake, lake_path):
    assert lake.committed_dates() == [DAY_1, DAY_2]

    manifest_file(lake_path, DAY_2).unlink()  # e.g. an ingestion rerun of DAY_2 is in flight
    assert lake.committed_dates() == [DAY_1]


def test_read_manifest_versions_the_commit_by_content_hash(lake, lake_path):
    batch = lake.read_manifest(DAY_1)

    assert batch.reference_date == DAY_1
    assert batch.manifest_sha256 == hashlib.sha256(manifest_file(lake_path, DAY_1).read_bytes()).hexdigest()
    assert set(batch.entities) == {"customers", "credit_operations", "installments", "transactions"}


def test_read_manifest_of_uncommitted_date_is_none(lake, lake_path):
    manifest_file(lake_path, DAY_2).unlink()
    assert lake.read_manifest(DAY_2) is None


def test_lake_names_are_allow_listed_before_reaching_sql():
    assert safe_identifier("credit_operations") == "credit_operations"
    for hostile in ["customers; DROP TABLE raw.customers", "Customers", "raw.customers", "", "a" * 64]:
        with pytest.raises(ValueError):
            safe_identifier(hostile)
    assert quote_literal("it's") == "'it''s'"
