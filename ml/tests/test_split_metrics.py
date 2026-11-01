import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.metrics import calibration_table, evaluate, ks_statistic
from ml.split import DEFAULT_DPD, DEFAULT_WINDOW_INSTALLMENTS, max_mature_contract_date, out_of_time_split

OBSERVATION = date(2026, 10, 15)


def _dbt_matures(contract: date, observation: date, window: int = 12, dpd: int = 90) -> bool:
    """dbt maturity rule: contract + window months + dpd days <= observation."""
    return (
        pd.Timestamp(contract) + pd.DateOffset(months=window) + pd.Timedelta(days=dpd)
    ).date() <= observation


@pytest.mark.parametrize("observation", [date(2026, 10, 15), date(2026, 3, 10), date(2025, 12, 1)])
def test_max_mature_contract_date_is_the_last_date_the_dbt_rule_calls_mature(observation):
    last = max_mature_contract_date(observation)
    assert _dbt_matures(last, observation)
    assert not _dbt_matures(last + timedelta(days=1), observation)


def test_max_mature_contract_date_comes_from_the_observation_clock():
    assert max_mature_contract_date(OBSERVATION) == date(2025, 7, 17)
    assert max_mature_contract_date(OBSERVATION, window_installments=6) == date(2026, 1, 17)


def test_maturity_parameters_match_the_dbt_vars():
    project = (Path(__file__).resolve().parents[2] / "dbt" / "credit_risk" / "dbt_project.yml").read_text(
        encoding="utf-8"
    )
    window = re.search(r"^\s*pd_window_installments:\s*(\d+)", project, re.M)
    dpd = re.search(r"^\s*pd_default_dpd:\s*(\d+)", project, re.M)
    assert int(window.group(1)) == DEFAULT_WINDOW_INSTALLMENTS
    assert int(dpd.group(1)) == DEFAULT_DPD


@pytest.mark.parametrize("test_months", [2, 3])
def test_out_of_time_split_never_mixes_periods(synthetic_population, test_months):
    last = max_mature_contract_date(OBSERVATION)
    train, test = out_of_time_split(synthetic_population, last, test_months=test_months)

    test_start = (pd.Timestamp(last) - pd.DateOffset(months=test_months)).date()
    assert pd.to_datetime(train["contract_date"]).max().date() <= test_start
    assert pd.to_datetime(test["contract_date"]).min().date() > test_start
    assert pd.to_datetime(test["contract_date"]).max().date() <= last
    assert set(train["operation_id"]).isdisjoint(test["operation_id"])
    assert len(train) + len(test) == len(synthetic_population)


def test_contracts_after_the_maturity_limit_are_refused(synthetic_population):
    late = synthetic_population.head(1).assign(contract_date=pd.Timestamp("2025-07-18"))
    with pytest.raises(ValueError, match="not mature"):
        out_of_time_split(pd.concat([synthetic_population, late]), max_mature_contract_date(OBSERVATION))


def test_a_period_without_defaults_is_refused(synthetic_population):
    last = max_mature_contract_date(OBSERVATION)
    test_start = (pd.Timestamp(last) - pd.DateOffset(months=3)).date()
    in_test = pd.to_datetime(synthetic_population["contract_date"]).dt.date > test_start
    no_defaults_in_test = synthetic_population.assign(
        pd_label=np.where(in_test, 0, synthetic_population["pd_label"])
    )
    with pytest.raises(ValueError, match="test period"):
        out_of_time_split(no_defaults_in_test, last)


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
