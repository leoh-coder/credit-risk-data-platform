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


def test_row_level_check_is_reported_once_not_per_column(clean_batch):
    tx = clean_batch["transactions"].reset_index(drop=True)
    tx.loc[0, "transaction_ts"] = tx.loc[0, "_extracted_at"] + pd.Timedelta(days=30)
    tx.loc[1, "amount"] = -5.0

    reasons = validate_entity("transactions", tx).quarantine["_failure_reasons"]

    assert reasons.loc[0] == "row:no_future_transactions"
    assert reasons.loc[1] == "amount:greater_than(0)"


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


def _with_bad_rows(df, n):
    df = df.copy().reset_index(drop=True)
    df.loc[df.index[:n], "principal_amount"] = -1.0
    return df


def test_small_batch_below_the_absolute_floor_is_quarantined_not_rejected(clean_batch):
    small = clean_batch["credit_operations"].head(20)
    result = validate_entity("credit_operations", _with_bad_rows(small, 5))  # 25% but only 5 rows
    assert len(result.quarantine) == 5
    assert len(result.valid) == 15


def test_batch_is_rejected_once_both_the_ratio_and_the_floor_are_exceeded(clean_batch):
    small = clean_batch["credit_operations"].head(20)
    with pytest.raises(ContractViolationError, match="error budget"):
        validate_entity("credit_operations", _with_bad_rows(small, 6))  # 30% and 6 > 5 rows


def test_many_bad_rows_under_the_ratio_are_not_rejected(clean_batch):
    large = clean_batch["installments"].head(200).copy().reset_index(drop=True)
    large.loc[large.index[:15], "amount_due"] = -1.0  # 15 rows > floor, but 7.5% < 10%
    result = validate_entity("installments", large)
    assert len(result.quarantine) == 15
