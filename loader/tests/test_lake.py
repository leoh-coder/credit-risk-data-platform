import hashlib
import json

import duckdb
import pytest

from loader.lake import ENTITIES, Lake, LoadError
from loader.sql import quote_literal
from tests.conftest import BUCKET, DAY_1, DAY_2, edit_manifest, manifest_file

# Every entity is listed, so validation reaches the row counts: one of them is a string.
STRING_ROW_COUNT = json.dumps(
    {
        "batch_id": "b",
        "contract_version": "1",
        "entities": {entity: {"rows_valid": "10" if entity == "customers" else 10} for entity in ENTITIES},
    }
)


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


def test_manifest_entities_must_be_exactly_the_allow_listed_ones(lake, lake_path):
    assert set(lake.read_manifest(DAY_1).entities) == set(ENTITIES)

    edit_manifest(lake_path, DAY_1, lambda m: m["entities"].update(accounts=m["entities"]["customers"]))
    with pytest.raises(LoadError, match="entities"):
        lake.read_manifest(DAY_1)

    edit_manifest(lake_path, DAY_2, lambda m: m["entities"].pop("installments"))
    with pytest.raises(LoadError, match="entities"):
        lake.read_manifest(DAY_2)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "no entities object"),
        ('{"batch_id": "b", "contract_version": "1"}', "no entities object"),
        (STRING_ROW_COUNT, "rows_valid is not a row count"),
    ],
)
def test_malformed_manifest_raises_a_load_error(lake, lake_path, content, reason):
    manifest_file(lake_path, DAY_1).write_text(content)
    with pytest.raises(LoadError, match=reason):
        lake.read_manifest(DAY_1)


def test_literals_are_quoted_for_duckdb():
    assert quote_literal("it's") == "'it''s'"
