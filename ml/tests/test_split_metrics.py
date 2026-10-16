from datetime import date

import numpy as np
import pandas as pd
import pytest

from ml.metrics import calibration_table, evaluate, ks_statistic
from ml.split import MAX_CONTRACT_DATE, TEST_START, out_of_time_split


def test_out_of_time_split_never_mixes_periods(synthetic_population):
    train, test = out_of_time_split(synthetic_population)

    assert pd.to_datetime(train["contract_date"]).max().date() < TEST_START
    assert pd.to_datetime(test["contract_date"]).min().date() >= TEST_START
    assert pd.to_datetime(test["contract_date"]).max().date() <= MAX_CONTRACT_DATE
    assert set(train["operation_id"]).isdisjoint(test["operation_id"])
    assert len(train) + len(test) == len(synthetic_population)


def test_contracts_after_the_maturity_limit_are_refused(synthetic_population):
    late = synthetic_population.head(1).assign(contract_date=pd.Timestamp("2025-07-18"))
    with pytest.raises(ValueError, match="not mature"):
        out_of_time_split(pd.concat([synthetic_population, late]))


def test_a_period_without_defaults_is_refused(synthetic_population):
    in_test = pd.to_datetime(synthetic_population["contract_date"]).dt.date >= TEST_START
    no_defaults_in_test = synthetic_population.assign(
        pd_label=np.where(in_test, 0, synthetic_population["pd_label"])
    )
    with pytest.raises(ValueError, match="test period"):
        out_of_time_split(no_defaults_in_test)


def test_ks_gini_auc_on_a_hand_computed_example():
    y = [0, 0, 1, 1]
    score = [0.1, 0.4, 0.35, 0.8]
    metrics = evaluate(y, score)
    assert metrics["auc"] == pytest.approx(0.75)
    assert metrics["gini"] == pytest.approx(0.5)
    assert metrics["ks"] == pytest.approx(0.5)  # at threshold 0.8: TPR 0.5, FPR 0
    assert ks_statistic([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)


def test_calibration_table_covers_every_row_and_orders_bins():
    rng = np.random.default_rng(0)
    pd_hat = rng.uniform(0, 1, 1000)
    y = (rng.uniform(0, 1, 1000) < pd_hat).astype(int)
    table = calibration_table(y, pd_hat)
    assert table["n"].sum() == 1000
    assert len(table) == 10
    assert table["mean_pd"].is_monotonic_increasing
    assert (table["observed_rate"] - table["mean_pd"]).abs().max() < 0.1  # well calibrated by construction


def test_split_dates_match_the_approved_decision():
    assert (date(2025, 4, 1), date(2025, 7, 17)) == (TEST_START, MAX_CONTRACT_DATE)
