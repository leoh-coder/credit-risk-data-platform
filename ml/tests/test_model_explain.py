from datetime import date

import numpy as np
import pandas as pd
import pytest

from ml.explain import contributions, odds_ratios, permutation_importances
from ml.features import FEATURES, REFERENCE_CATEGORIES, TARGET, feature_matrix
from ml.metrics import evaluate
from ml.model import gradient_boosting_pipeline, logistic_pipeline
from ml.split import max_mature_contract_date, out_of_time_split


@pytest.fixture(scope="module")
def fitted(synthetic_population):
    train, _, test = out_of_time_split(synthetic_population, max_mature_contract_date(date(2026, 10, 15)))
    model = logistic_pipeline().fit(feature_matrix(train), train[TARGET])
    return model, train, test


def test_logistic_learns_the_planted_signal_out_of_time(fitted):
    model, _, test = fitted
    metrics = evaluate(test[TARGET], model.predict_proba(feature_matrix(test))[:, 1])
    assert metrics["auc"] > 0.7


def test_comparison_model_trains_on_the_same_frame(fitted):
    _, train, test = fitted
    hgb = gradient_boosting_pipeline().fit(feature_matrix(train), train[TARGET])
    assert evaluate(test[TARGET], hgb.predict_proba(feature_matrix(test))[:, 1])["auc"] > 0.65


def test_unseen_category_and_missing_values_do_not_break_scoring(fitted):
    model, _, test = fitted
    odd = feature_matrix(test).head(1).assign(employment_type="never_seen", monthly_income=np.nan)
    with pytest.warns(UserWarning, match="unknown categories"):
        pd_hat = model.predict_proba(odd)[:, 1]
    assert 0 < pd_hat[0] < 1
    # an unseen level is encoded like the reference category (all zeros)
    as_reference = odd.assign(employment_type=REFERENCE_CATEGORIES["employment_type"])
    assert pd_hat[0] == pytest.approx(model.predict_proba(as_reference)[:, 1][0])


def test_model_never_receives_outcome_columns(fitted):
    model, _, _ = fitted
    assert list(model.feature_names_in_) == list(FEATURES)
    assert "max_dpd_in_window" not in model.feature_names_in_


def test_odds_ratios_recover_the_planted_directions(fitted):
    model, _, _ = fitted
    table = odds_ratios(model).set_index("input")
    assert table.loc["num__bureau_score_at_origination", "odds_ratio"] < 1  # protective
    assert table.loc["num__of_betting_spend_share", "odds_ratio"] > 1  # risky


def test_contributions_add_up_exactly_to_the_model_log_odds(fitted):
    model, _, test = fitted
    row = feature_matrix(test).head(1)
    parts = contributions(model, row)
    assert parts.index.tolist() == ["intercept", *FEATURES]
    assert parts.sum() == pytest.approx(model.decision_function(row)[0])


def test_permutation_importance_ranks_signal_above_noise(fitted):
    model, _, test = fitted
    importance = permutation_importances(model, test, test[TARGET], n_repeats=5).set_index("feature")
    signal = importance.loc[["bureau_score_at_origination", "of_betting_spend_share"], "auc_drop_mean"]
    noise = importance.loc[["of_tx_count", "age_at_registration"], "auc_drop_mean"]
    assert signal.min() > noise.max()


def test_contributions_refuse_more_than_one_row(fitted):
    model, _, test = fitted
    with pytest.raises(ValueError):
        contributions(model, pd.concat([feature_matrix(test).head(2)]))


def test_reference_categories_have_no_coefficient(fitted):
    model, _, _ = fitted
    inputs = set(odds_ratios(model)["input"])
    for feature, reference in REFERENCE_CATEGORIES.items():
        assert f"cat__{feature}_{reference}" not in inputs
        assert any(i.startswith(f"cat__{feature}_") for i in inputs)  # the other levels are still there


def test_a_reference_category_contributes_nothing(fitted):
    model, _, test = fitted
    row = feature_matrix(test).head(1).assign(**REFERENCE_CATEGORIES)
    parts = contributions(model, row)
    for feature in REFERENCE_CATEGORIES:
        assert parts[feature] == pytest.approx(0.0)
