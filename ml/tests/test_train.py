import json
import re

import joblib
import pandas as pd

from ml.data import FEATURE_SOURCE, population_sql
from ml.features import FEATURES, FORBIDDEN_COLUMNS, feature_matrix
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


def test_model_card_states_the_validation_and_the_numbers(synthetic_population, tmp_path):
    report = train(synthetic_population, tmp_path)
    card = (tmp_path / "model_card.md").read_text()
    assert "out of time" in card
    assert f"{report['models']['logistic_regression']['test']['auc']:.3f}" in card


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
    assert ":max_contract_date" in sql


def test_calibration_artifact_has_ten_bins(synthetic_population, tmp_path):
    train(synthetic_population, tmp_path)
    assert len(pd.read_csv(tmp_path / "calibration.csv")) == 10
