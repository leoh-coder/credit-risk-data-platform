import json
from pathlib import Path

import pyarrow.dataset as ds
import pytest

from ingestion import main
from ingestion.contracts import ContractViolationError
from ingestion.main import run_batch
from ingestion.writer import LakeWriter
from tests.conftest import AS_OF, REFERENCE_DATE


def _lake(settings) -> Path:
    return Path(settings.lake_local_path) / settings.lake_bucket


def _manifest_path(settings) -> Path:
    return _lake(settings) / "_manifests" / f"reference_date={REFERENCE_DATE}" / "manifest.json"


class _SpyWriter(LakeWriter):
    """Records whether the commit marker was visible at the moment each partition was written."""

    def __init__(self, settings):
        super().__init__(settings)
        self._settings = settings
        self.manifest_visible_during_writes: list[bool] = []

    def write_partition(self, zone, entity, reference_date, df):
        self.manifest_visible_during_writes.append(_manifest_path(self._settings).exists())
        return super().write_partition(zone, entity, reference_date, df)


def test_batch_lands_partitioned_parquet_and_manifest(settings):
    manifest = run_batch(REFERENCE_DATE, AS_OF, settings, LakeWriter(settings))
    lake = _lake(settings)

    for entity, stats in manifest["entities"].items():
        partition = lake / "raw" / entity / f"reference_date={REFERENCE_DATE}"
        dataset = ds.dataset(partition, format="parquet")
        assert dataset.count_rows() == stats["rows_valid"] > 0

    manifest_path = lake / "_manifests" / f"reference_date={REFERENCE_DATE}" / "manifest.json"
    stored = json.loads(manifest_path.read_text())
    assert stored["batch_id"] == manifest["batch_id"]
    assert stored["contract_version"] == "1.0.0"


def test_rerun_is_idempotent(settings):
    writer = LakeWriter(settings)
    first = run_batch(REFERENCE_DATE, AS_OF, settings, writer)
    second = run_batch(REFERENCE_DATE, AS_OF, settings, writer)
    assert first["entities"] == second["entities"]

    files = list((_lake(settings) / "raw" / "customers").rglob("*.parquet"))
    assert len(files) == 1  # overwritten, not appended


def test_rerun_hides_manifest_while_partitions_are_rewritten(settings):
    run_batch(REFERENCE_DATE, AS_OF, settings, LakeWriter(settings))
    assert _manifest_path(settings).exists()

    spy = _SpyWriter(settings)
    run_batch(REFERENCE_DATE, AS_OF, settings, spy)

    # A loader polling for manifests must never see a commit marker next to a half-rewritten partition.
    assert spy.manifest_visible_during_writes
    assert not any(spy.manifest_visible_during_writes)
    assert _manifest_path(settings).exists()


def test_rejected_rerun_keeps_previous_commit(settings, monkeypatch):
    first = run_batch(REFERENCE_DATE, AS_OF, settings, LakeWriter(settings))

    def reject(entity, df, **_):
        raise ContractViolationError(f"[{entity}] simulated schema drift")

    monkeypatch.setattr(main, "validate_entity", reject)
    with pytest.raises(ContractViolationError):
        run_batch(REFERENCE_DATE, AS_OF, settings, LakeWriter(settings))

    stored = json.loads(_manifest_path(settings).read_text())
    assert stored["batch_id"] == first["batch_id"]
    customers = _lake(settings) / "raw" / "customers" / f"reference_date={REFERENCE_DATE}"
    expected_rows = first["entities"]["customers"]["rows_valid"]
    assert ds.dataset(customers, format="parquet").count_rows() == expected_rows
