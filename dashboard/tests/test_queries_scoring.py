import re

import numpy as np
import pandas as pd
import pytest

from dashboard.queries import QUERIES
from dashboard.scoring import (
    FIELD_META,
    RISK_BANDS,
    applicant_frame,
    load_artifacts,
    risk_band,
    score,
    simulation_fields,
)
from ml.features import DERIVED_FEATURES, FEATURES, NUMERIC_FEATURES


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_every_query_reads_the_gold_layer_only(name):
    relations = re.findall(r"\b(?:from|join)\s+([a-z_]+\.[a-z_]+|[a-z_]+)", QUERIES[name], re.I)
    assert relations, name
    assert all(r.startswith("marts.") for r in relations), relations


def test_no_query_touches_outcome_columns_as_inputs_to_scoring():
    # the dashboard may *report* labels, but scoring only ever goes through the allowlist
    row = applicant_frame(dict.fromkeys(FEATURES, 1) | {"product_type": "x", "employment_type": "y"})
    assert list(row.columns) == list(FEATURES)


def test_reference_ratio_is_derived_not_typed():
    values = dict.fromkeys(FEATURES, 1.0) | {
        "principal_amount": 10_000.0,
        "installments_count": 12,
        "monthly_income": 5_000.0,
    }
    values["reference_installment_to_income_ratio"] = 99.0  # anything typed is overwritten
    assert applicant_frame(values).at[0, "reference_installment_to_income_ratio"] == pytest.approx(0.2009, abs=1e-4)
    no_income = applicant_frame(values | {"monthly_income": 0.0})
    assert np.isnan(no_income.at[0, "reference_installment_to_income_ratio"])


@pytest.mark.parametrize(
    ("pd_hat", "band"), [(0.0, "A"), (0.0199, "A"), (0.02, "B"), (0.099, "C"), (0.10, "D"), (0.2, "E"), (1.0, "E")]
)
def test_risk_band_boundaries(pd_hat, band):
    assert risk_band(pd_hat) == band
    assert [b for b, _ in RISK_BANDS] == ["A", "B", "C", "D", "E"]


def test_missing_artifacts_mean_no_model_not_an_error(tmp_path):
    assert load_artifacts(tmp_path) is None


def test_score_returns_a_pd_and_contributions_that_add_up(artifacts_dir):
    artifacts = load_artifacts(artifacts_dir)
    defaults = artifacts.simulation_defaults
    values = defaults["numeric"] | {c: spec["default"] for c, spec in defaults["categorical"].items()}

    pd_hat, parts = score(artifacts.model, values)

    assert 0 < pd_hat < 1
    assert 1 / (1 + np.exp(-parts.sum())) == pytest.approx(pd_hat)
    assert parts.index.tolist() == ["intercept", *FEATURES]


def test_a_lower_bureau_score_raises_the_simulated_pd(artifacts_dir):
    artifacts = load_artifacts(artifacts_dir)
    defaults = artifacts.simulation_defaults
    values = defaults["numeric"] | {c: spec["default"] for c, spec in defaults["categorical"].items()}
    low, _ = score(artifacts.model, values | {"bureau_score_at_origination": 300})
    high, _ = score(artifacts.model, values | {"bureau_score_at_origination": 900})
    assert low > high


def test_fetch_turns_decimals_into_floats(monkeypatch):
    from decimal import Decimal

    import dashboard.queries as queries

    class FakeEngine:
        def connect(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def dispose(self):
            pass

    monkeypatch.setattr(queries, "create_engine", lambda url: FakeEngine())
    monkeypatch.setattr(queries.pd, "read_sql", lambda *a, **k: pd.DataFrame({"x": [Decimal("1.50")], "y": ["a"]}))
    frame = queries.fetch("label_status", "postgresql://unused")
    assert frame["x"].dtype == float
    assert frame["y"].tolist() == ["a"]


def test_every_non_derived_numeric_feature_has_a_simulation_field():
    typed = {field.feature for field in simulation_fields()}
    assert typed == set(NUMERIC_FEATURES) - set(DERIVED_FEATURES)


def test_derived_features_are_computed_from_typed_fields():
    typed = {field.feature for field in simulation_fields()}
    for inputs in DERIVED_FEATURES.values():
        assert set(inputs) <= typed


def test_field_metadata_has_no_stale_entries():
    assert set(FIELD_META) <= set(NUMERIC_FEATURES) - set(DERIVED_FEATURES)


def test_a_new_feature_without_metadata_still_gets_a_field():
    fields = simulation_fields(numeric_features=(*NUMERIC_FEATURES, "brand_new_ratio"))
    new = next(field for field in fields if field.feature == "brand_new_ratio")
    assert new.label == "Brand new ratio"
    assert new.min_value <= new.max_value
