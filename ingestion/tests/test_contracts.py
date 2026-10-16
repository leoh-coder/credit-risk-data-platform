import pandas as pd
import pytest

from ingestion.contracts import ContractViolationError, validate_entity
from ingestion.generator import inject_anomalies
from tests.conftest import REFERENCE_DATE


def test_clean_batch_passes_every_contract(clean_batch):
    for entity, df in clean_batch.items():
        result = validate_entity(entity, df)
        assert result.quarantine.empty
        assert len(result.valid) == len(df)


def test_dirty_rows_are_quarantined_with_reasons(clean_batch):
    dirty, injected = inject_anomalies(clean_batch, rate=0.05, seed=1, reference_date=REFERENCE_DATE)
    for entity, df in dirty.items():
        result = validate_entity(entity, df, max_invalid_ratio=0.5)
        assert len(result.valid) + len(result.quarantine) == len(df)
        assert len(result.quarantine) == injected[entity]
        assert result.quarantine["_failure_reasons"].str.len().gt(0).all()


def test_duplicate_keys_keep_first_occurrence(clean_batch):
    customers = clean_batch["customers"]
    with_dupe = pd.concat([customers, customers.iloc[[0]]], ignore_index=True)
    result = validate_entity("customers", with_dupe)
    assert len(result.quarantine) == 1
    assert result.quarantine.index[0] == len(customers)  # the repeat, not the original
    assert result.valid["customer_id"].is_unique


def test_missing_column_is_a_hard_failure(clean_batch):
    with pytest.raises(ContractViolationError, match="schema-level"):
        validate_entity("customers", clean_batch["customers"].drop(columns="state"))


def test_unexpected_column_is_a_hard_failure(clean_batch):
    with pytest.raises(ContractViolationError, match="schema-level"):
        validate_entity("customers", clean_batch["customers"].assign(gender="M"))


def test_error_budget_circuit_breaker(clean_batch):
    df = clean_batch["credit_operations"].copy()
    df.loc[df.index[: len(df) // 2], "principal_amount"] = -1.0
    with pytest.raises(ContractViolationError, match="error budget"):
        validate_entity("credit_operations", df, max_invalid_ratio=0.10)
