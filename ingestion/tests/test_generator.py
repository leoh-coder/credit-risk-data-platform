from datetime import date

import pandas as pd
import pytest

from ingestion.generator import generate_batch
from tests.conftest import AS_OF, REFERENCE_DATE


def test_same_inputs_produce_identical_batches(clean_batch):
    again = generate_batch(REFERENCE_DATE, AS_OF, seed=7, customers_per_batch=60)
    for entity, df in clean_batch.items():
        pd.testing.assert_frame_equal(df, again[entity])


def test_different_dates_produce_different_batches(clean_batch):
    other = generate_batch(date(2025, 6, 2), AS_OF, seed=7, customers_per_batch=60)
    assert set(clean_batch["customers"]["customer_id"]).isdisjoint(other["customers"]["customer_id"])


def test_referential_integrity(clean_batch):
    customers = set(clean_batch["customers"]["customer_id"])
    operations = set(clean_batch["credit_operations"]["operation_id"])
    assert set(clean_batch["credit_operations"]["customer_id"]) <= customers
    assert set(clean_batch["transactions"]["customer_id"]) <= customers
    assert set(clean_batch["installments"]["operation_id"]) == operations


def test_installment_schedule_matches_contract_terms(clean_batch):
    counts = clean_batch["installments"].groupby("operation_id").size()
    terms = clean_batch["credit_operations"].set_index("operation_id")["installments_count"]
    pd.testing.assert_series_equal(counts.sort_index(), terms.sort_index(), check_names=False)


def test_payments_are_never_observed_after_as_of(clean_batch):
    paid = clean_batch["installments"]["payment_date"].dropna()
    assert (paid <= pd.Timestamp(AS_OF)).all()


def test_behavioral_history_precedes_application(clean_batch):
    assert (clean_batch["transactions"]["transaction_ts"] < pd.Timestamp(REFERENCE_DATE)).all()


def test_latent_risk_is_not_leaked(clean_batch):
    for df in clean_batch.values():
        assert not {"z", "latent_risk", "will_default"} & set(df.columns)


def test_as_of_before_reference_date_is_rejected():
    with pytest.raises(ValueError):
        generate_batch(date(2026, 1, 10), date(2026, 1, 1))
