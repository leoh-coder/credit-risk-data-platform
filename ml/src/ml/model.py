"""Model pipelines. The logistic regression is the main model (scorecard standard, interpretable);
HistGradientBoosting is the single comparison model. Both take the raw allowlisted frame."""

from __future__ import annotations

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, OrdinalEncoder, StandardScaler

from ml.features import CATEGORICAL_FEATURES, MONEY_FEATURES, NUMERIC_FEATURES

RANDOM_STATE = 42
_OTHER_NUMERIC = tuple(c for c in NUMERIC_FEATURES if c not in MONEY_FEATURES)


def logistic_pipeline() -> Pipeline:
    money = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
            ("scale", StandardScaler()),
        ]
    )
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    categorical = OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=50, sparse_output=False)
    prep = ColumnTransformer(
        [
            ("money", money, list(MONEY_FEATURES)),
            ("num", numeric, list(_OTHER_NUMERIC)),
            ("cat", categorical, list(CATEGORICAL_FEATURES)),
        ]
    )
    return Pipeline([("prep", prep), ("model", LogisticRegression(C=1.0, max_iter=2000))])


def gradient_boosting_pipeline() -> Pipeline:
    encoder = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1)
    prep = ColumnTransformer(
        [("cat", encoder, list(CATEGORICAL_FEATURES)), ("num", "passthrough", list(NUMERIC_FEATURES))]
    )
    model = HistGradientBoostingClassifier(
        categorical_features=list(range(len(CATEGORICAL_FEATURES))), random_state=RANDOM_STATE
    )
    return Pipeline([("prep", prep), ("model", model)])
