import json
from collections import Counter
from dataclasses import replace

import duckdb

from loader.lake import Lake
from loader.main import run
from loader.warehouse import Warehouse
from tests.conftest import DAY_1, DAY_2, edit_manifest, manifest_file, query

ENTITIES = ("customers", "credit_operations", "installments", "transactions")


def committed_rows(lake_path, day) -> dict[str, int]:
    manifest = json.loads(manifest_file(lake_path, day).read_text())
    return {entity: stats["rows_valid"] for entity, stats in manifest["entities"].items()}


def raw_rows(settings, entity, day) -> int:
    return query(settings, f"select count(*) from raw.{entity} where _reference_date = %s", [day])[0][0]


def load_ids(settings, day) -> set[str]:
    return {
        row[0]
        for row in query(
            settings, "select distinct _load_id from raw.customers where _reference_date = %s", [day]
        )
    }


def simulate_ingestion_rerun(lake_path, day) -> None:
    edit_manifest(lake_path, day, lambda m: m.update(written_at="2026-10-16T09:00:00+00:00"))


def test_first_run_loads_every_committed_date_and_audits_it(settings, lake_path):
    report = run(settings)

    assert report.loaded == [DAY_1, DAY_2]
    assert report.failed == []
    for day in (DAY_1, DAY_2):
        for entity, expected in committed_rows(lake_path, day).items():
            assert raw_rows(settings, entity, day) == expected > 0

    audit = query(settings, "select reference_date, rows_loaded from audit.lake_loads order by 1")
    assert [day for day, _ in audit] == [DAY_1, DAY_2]
    assert audit[0][1] == committed_rows(lake_path, DAY_1)

    orphans = query(
        settings,
        "select count(*) from raw.transactions t "
        "left join audit.lake_loads a on a.load_id::text = t._load_id where a.load_id is null",
    )
    assert orphans == [(0,)]  # every raw row traces back to its audit record

    columns = {
        row[0]
        for row in query(
            settings,
            "select column_name from information_schema.columns "
            "where table_schema = 'raw' and table_name = 'customers'",
        )
    }
    assert {"_batch_id", "_reference_date", "_load_id", "_loaded_at"} <= columns
    assert "reference_date" not in columns  # hive partition key not duplicated


def test_second_run_is_a_noop(settings):
    run(settings)
    report = run(settings)

    assert report.loaded == []
    assert report.up_to_date == [DAY_1, DAY_2]
    assert query(settings, "select count(*) from audit.lake_loads") == [(2,)]


def test_date_without_manifest_is_never_loaded(settings, lake_path):
    manifest_file(lake_path, DAY_2).unlink()  # partitions are there, the commit marker is not

    report = run(settings)

    assert report.loaded == [DAY_1]
    for entity in ENTITIES:
        assert raw_rows(settings, entity, DAY_2) == 0


def test_lake_rerun_reloads_only_that_date_without_duplicates(settings, lake_path):
    run(settings)
    first_load = load_ids(settings, DAY_1)

    simulate_ingestion_rerun(lake_path, DAY_1)
    report = run(settings)

    assert report.loaded == [DAY_1]
    assert report.up_to_date == [DAY_2]
    for entity, expected in committed_rows(lake_path, DAY_1).items():
        assert raw_rows(settings, entity, DAY_1) == expected  # replaced, not appended
    assert load_ids(settings, DAY_1).isdisjoint(first_load)
    assert len(load_ids(settings, DAY_1)) == 1


def test_reconciliation_mismatch_rolls_back_the_whole_date(settings, lake_path):
    def overstate_transactions(manifest):
        manifest["entities"]["transactions"]["rows_valid"] += 1

    edit_manifest(lake_path, DAY_2, overstate_transactions)  # transactions is the LAST entity loaded

    report = run(settings)

    assert report.loaded == [DAY_1]
    assert report.failed == [DAY_2]
    for entity in ENTITIES:  # customers/operations/installments were inserted, then rolled back
        assert raw_rows(settings, entity, DAY_2) == 0
    assert query(settings, "select count(*) from audit.lake_loads where reference_date = %s", [DAY_2]) == [
        (0,)
    ]

    edit_manifest(
        lake_path,
        DAY_2,
        lambda m: m["entities"]["transactions"].update(
            rows_valid=m["entities"]["transactions"]["rows_valid"] - 1
        ),
    )
    assert run(settings).loaded == [DAY_2]  # fixed lake -> date was still pending


def test_crash_before_commit_keeps_the_previous_load(settings, lake_path, monkeypatch):
    run(settings)
    previous = {entity: raw_rows(settings, entity, DAY_1) for entity in ENTITIES}
    previous_load = load_ids(settings, DAY_1)

    def crash(*_args, **_kwargs):
        raise duckdb.IOException("simulated crash right before COMMIT")

    monkeypatch.setattr(Warehouse, "_record_load", crash)
    simulate_ingestion_rerun(lake_path, DAY_1)
    report = run(settings)

    assert report.failed == [DAY_1]
    assert {entity: raw_rows(settings, entity, DAY_1) for entity in ENTITIES} == previous
    assert load_ids(settings, DAY_1) == previous_load  # the DELETE was rolled back too


def test_manifest_changed_during_load_is_rolled_back(settings, monkeypatch):
    original = Lake.read_manifest
    calls = Counter()

    def rerun_starts_mid_load(self, day):
        calls[day] += 1
        batch = original(self, day)
        if day == DAY_2 and calls[day] == 2:  # the re-check after the inserts
            return replace(batch, manifest_sha256="0" * 64)
        return batch

    monkeypatch.setattr(Lake, "read_manifest", rerun_starts_mid_load)
    report = run(settings)

    assert report.failed == [DAY_2]
    assert raw_rows(settings, "customers", DAY_2) == 0


def test_hostile_entity_name_in_manifest_never_reaches_sql(settings, lake_path):
    run(settings)

    def inject(manifest):
        manifest["entities"]["customers; DROP TABLE raw.customers"] = manifest["entities"].pop("customers")

    edit_manifest(lake_path, DAY_2, inject)
    simulate_ingestion_rerun(lake_path, DAY_2)
    report = run(settings)

    assert report.failed == [DAY_2]
    assert raw_rows(settings, "customers", DAY_1) > 0  # table still there
