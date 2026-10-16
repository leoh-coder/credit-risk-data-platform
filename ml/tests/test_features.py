import pandas as pd
import pytest

from ml.features import (
    CATEGORICAL_FEATURES,
    EXCLUDED_ON_PURPOSE,
    FEATURES,
    FORBIDDEN_COLUMNS,
    MONEY_FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    LeakageError,
    check_feature_list,
    feature_matrix,
)

# Outcome columns that must stay forbidden, whatever else changes.
OUTCOME_COLUMNS = {
    "max_dpd_in_window",
    "has_defaulted_to_date",
    "is_mature",
    "window_installments",
    "window_installments_present",
    "is_window_complete",
    "window_last_due_date",
    "label_status",
    "pd_label",
}


def test_outcome_columns_are_all_forbidden():
    assert OUTCOME_COLUMNS <= FORBIDDEN_COLUMNS


def test_allowlist_and_forbidden_list_do_not_overlap():
    assert not set(FEATURES) & FORBIDDEN_COLUMNS
    assert TARGET not in FEATURES


def test_allowlist_is_partitioned_into_numeric_and_categorical():
    assert set(FEATURES) == set(NUMERIC_FEATURES) | set(CATEGORICAL_FEATURES)
    assert not set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES)
    assert set(MONEY_FEATURES) <= set(NUMERIC_FEATURES)
    assert len(FEATURES) == len(set(FEATURES))


@pytest.mark.parametrize("column", sorted(FORBIDDEN_COLUMNS))
def test_any_forbidden_column_in_a_feature_list_raises(column):
    with pytest.raises(LeakageError, match=column):
        check_feature_list([*FEATURES, column])


@pytest.mark.parametrize("column", sorted(EXCLUDED_ON_PURPOSE))
def test_deliberately_excluded_columns_never_enter_the_model(column):
    # pricing feedback loop or socio-economic / geographic proxy
    assert column not in FEATURES
    with pytest.raises(LeakageError, match="not in the allowlist"):
        check_feature_list([*FEATURES, column])


def test_columns_outside_the_allowlist_raise_even_if_harmless_looking():
    with pytest.raises(LeakageError, match="not in the allowlist"):
        check_feature_list([*FEATURES, "interest_rate_monthly"])  # excluded on purpose


def test_feature_matrix_keeps_exactly_the_allowlist_and_drops_everything_else():
    # A "canary" frame as it comes from the marts, carrying the target and outcome columns too.
    row = dict.fromkeys(FEATURES, 1)
    row |= dict.fromkeys(CATEGORICAL_FEATURES, "x")
    row |= {column: 1 for column in OUTCOME_COLUMNS} | {
        "operation_id": "OPR1",
        "contract_date": "2025-01-01",
    }
    frame = pd.DataFrame([row])

    X = feature_matrix(frame)

    assert list(X.columns) == list(FEATURES)


def test_feature_matrix_fails_loudly_when_an_allowlisted_column_is_missing():
    frame = pd.DataFrame([dict.fromkeys(FEATURES[1:], 1)])
    with pytest.raises(KeyError):
        feature_matrix(frame)
