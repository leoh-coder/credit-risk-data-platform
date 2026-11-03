import json
import logging
import re

import joblib
import pandas as pd
import pytest

import ml.train as ml_train
from ml.data import FEATURE_SOURCE, population_sql
from ml.features import EXCLUDED_ON_PURPOSE, FEATURES, FORBIDDEN_COLUMNS, TARGET, feature_matrix
from ml.metrics import evaluate
from ml.split import max_mature_contract_date, out_of_time_split
from ml.train import train

ARTIFACTS = {
    "model.joblib",
    "metrics.json",
    "coefficients.csv",
    "permutation_importance.csv",
    "calibration.csv",
    "simulation_defaults.json",
    "model_card.md",
}


def test_simulation_defaults_cover_every_feature(synthetic_population, tmp_path):
    train(synthetic_population, tmp_path)
    defaults = json.loads((tmp_path / "simulation_defaults.json").read_text())
    assert set(defaults["numeric"]) | set(defaults["categorical"]) == set(FEATURES)
    assert (
        defaults["categorical"]["product_type"]["default"]
        in defaults["categorical"]["product_type"]["options"]
    )


def test_training_writes_every_artifact_and_a_usable_model(synthetic_population, tmp_path):
    report = train(synthetic_population, tmp_path)

    assert {p.name for p in tmp_path.iterdir()} == ARTIFACTS
    model = joblib.load(tmp_path / "model.joblib")
    pd_hat = model.predict_proba(feature_matrix(synthetic_population.head(5)))[:, 1]
    assert ((pd_hat > 0) & (pd_hat < 1)).all()

    stored = json.loads((tmp_path / "metrics.json").read_text())
    assert stored["features"] == list(FEATURES)
    assert stored["split"]["type"] == "out_of_time"
    assert stored["split"]["train"]["to"] < stored["split"]["test"]["from"]
    assert report["models"]["logistic_regression"]["test"]["auc"] > 0.7


def test_model_card_states_the_validation_the_numbers_and_the_exclusions(synthetic_population, tmp_path):
    report = train(synthetic_population, tmp_path)
    card = (tmp_path / "model_card.md").read_text()
    assert "out of time" in card
    assert f"{report['models']['logistic_regression']['test']['auc']:.3f}" in card
    missing = [column for column in sorted(EXCLUDED_ON_PURPOSE) if f"`{column}`" not in card]
    assert not missing, f"excluded on purpose but not named in the model card: {missing}"


def test_population_query_selects_only_allowlisted_columns():
    sql = population_sql()
    selected = re.search(r"select(.*?)from marts", sql, re.S).group(1)
    for column in FORBIDDEN_COLUMNS - {"operation_id", "pd_label"}:  # id and target are fetched on purpose
        assert not re.search(rf"\b{column}\b", selected), column
    assert set(FEATURE_SOURCE) == set(FEATURES)


def test_population_query_filters_labeled_known_mature_contracts():
    sql = population_sql()
    assert "label_status = 'labeled'" in sql
    assert "not c.is_unknown_member" in sql


def test_calibration_artifact_has_ten_bins(synthetic_population, tmp_path):
    train(synthetic_population, tmp_path)
    assert len(pd.read_csv(tmp_path / "calibration.csv")) == 10


def test_contracts_after_the_maturity_limit_are_excluded_and_reported(synthetic_population, tmp_path, caplog):
    late = synthetic_population.head(50).assign(contract_date=pd.Timestamp("2025-09-01"), operation_id="late")
    late_defaults = int(late[TARGET].sum())
    with caplog.at_level(logging.INFO, logger="ml"):
        report = train(pd.concat([synthetic_population, late], ignore_index=True), tmp_path)

    assert report["split"]["max_contract_date"] == "2025-07-17"
    assert report["split"]["test"]["to"] <= "2025-07-17"
    periods = ("train", "validation", "test")
    assert sum(report["split"][p]["rows"] for p in periods) == len(synthetic_population)

    excluded = {"rows": 50, "defaults": late_defaults}
    assert report["split"]["excluded_after_maturity_limit"] == excluded
    stored = json.loads((tmp_path / "metrics.json").read_text())
    assert stored["split"]["excluded_after_maturity_limit"] == excluded
    assert f"50 contracts ({late_defaults} defaults)" in (tmp_path / "model_card.md").read_text()
    assert f"excluded 50 contracts ({late_defaults} defaults) after the maturity limit" in caplog.text


def _periods(population):
    last = max_mature_contract_date(pd.to_datetime(population["observed_at"]).min().date())
    return out_of_time_split(population, last)


def test_selection_and_importance_never_see_the_test_period(synthetic_population, tmp_path, monkeypatch):
    seen = []
    real_select, real_importance = ml_train.select, ml_train.permutation_importances

    def spy_select(build, grid, X_train, y_train, X_validation, y_validation):
        seen.extend([set(X_train.index), set(X_validation.index)])
        return real_select(build, grid, X_train, y_train, X_validation, y_validation)

    def spy_importance(pipeline, X, y, **kwargs):
        seen.append(set(X.index))
        return real_importance(pipeline, X, y, **kwargs)

    monkeypatch.setattr(ml_train, "select", spy_select)
    monkeypatch.setattr(ml_train, "permutation_importances", spy_importance)
    train(synthetic_population, tmp_path)

    _, validation, test = _periods(synthetic_population)
    assert seen and all(rows.isdisjoint(test.index) for rows in seen)
    assert seen[-1] == set(validation.index)  # importance is measured on the validation period


def test_final_model_is_refit_on_train_and_validation_and_scored_once_on_test(synthetic_population, tmp_path):
    report = train(synthetic_population, tmp_path)
    train_part, validation, test = _periods(synthetic_population)

    logistic = report["models"]["logistic_regression"]
    assert logistic["final_fit_rows"] == len(train_part) + len(validation)
    model = joblib.load(tmp_path / "model.joblib")
    auc = evaluate(test[TARGET], model.predict_proba(feature_matrix(test))[:, 1])["auc"]
    assert auc == pytest.approx(logistic["test"]["auc"])
    assert {"train", "validation", "test"} <= set(report["split"]) and {"train", "validation", "test"} <= set(
        logistic
    )


def test_the_order_of_the_population_rows_does_not_change_the_models(synthetic_population, tmp_path):
    # The warehouse returns rows in no fixed order; boosting's early stopping splits them by position.
    shuffled = synthetic_population.sample(frac=1, random_state=1)
    as_given = train(synthetic_population, tmp_path / "as_given")
    reordered = train(shuffled, tmp_path / "shuffled")
    assert reordered["models"] == as_given["models"]


@pytest.mark.parametrize("model", ["logistic_regression", "hist_gradient_boosting"])
def test_hyperparameters_are_chosen_by_validation_auc(synthetic_population, tmp_path, model):
    report = train(synthetic_population, tmp_path)
    candidates = report["models"][model]["candidates"]
    best = max(candidates, key=lambda c: c["validation_auc"])
    assert len(candidates) > 1
    assert report["models"][model]["params"] == best["params"]


def test_help_prints_usage_without_training(monkeypatch, capsys):
    def no_training(*_args, **_kwargs):
        raise AssertionError("--help must not read the marts")

    monkeypatch.setattr(ml_train, "load_population", no_training)
    with pytest.raises(SystemExit) as exit_info:
        ml_train.main(["--help"])
    assert exit_info.value.code == 0
    assert "usage:" in capsys.readouterr().out
