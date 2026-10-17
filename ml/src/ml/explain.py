"""Odds ratios, permutation importance and exact per-feature log-odds contributions."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.pipeline import Pipeline

from ml.features import CATEGORICAL_FEATURES, FEATURES


def _original_feature(transformed_name: str) -> str:
    """'money__principal_amount' -> 'principal_amount'; 'cat__product_type_payroll_loan' -> 'product_type'."""
    name = transformed_name.split("__", 1)[1]
    for categorical in CATEGORICAL_FEATURES:
        if name.startswith(categorical + "_"):
            return categorical
    return name


def odds_ratios(pipeline: Pipeline) -> pd.DataFrame:
    """Coefficient and odds ratio per model input (per +1 SD; log-SD for money; a category vs. the rest)."""
    names = pipeline.named_steps["prep"].get_feature_names_out()
    coefficients = pipeline.named_steps["model"].coef_[0]
    table = pd.DataFrame(
        {
            "input": names,
            "feature": [_original_feature(n) for n in names],
            "coefficient": coefficients,
            "odds_ratio": np.exp(coefficients),
        }
    )
    return table.sort_values("coefficient", key=np.abs, ascending=False, ignore_index=True)


def contributions(pipeline: Pipeline, row: pd.DataFrame) -> pd.Series:
    """Log-odds of one applicant as intercept + one term per feature (exact for a linear model)."""
    if len(row) != 1:
        raise ValueError("contributions are computed for exactly one row")
    z = np.asarray(pipeline.named_steps["prep"].transform(row[list(FEATURES)]))[0]
    names = pipeline.named_steps["prep"].get_feature_names_out()
    terms = pd.Series(z * pipeline.named_steps["model"].coef_[0], index=[_original_feature(n) for n in names])
    per_feature = terms.groupby(level=0).sum().reindex(list(FEATURES))
    return pd.concat([pd.Series({"intercept": pipeline.named_steps["model"].intercept_[0]}), per_feature])


def permutation_importances(pipeline: Pipeline, X: pd.DataFrame, y, n_repeats: int = 10) -> pd.DataFrame:
    """AUC drop when one raw feature is shuffled."""
    result = permutation_importance(
        pipeline, X[list(FEATURES)], y, scoring="roc_auc", n_repeats=n_repeats, random_state=42
    )
    return pd.DataFrame(
        {
            "feature": list(FEATURES),
            "auc_drop_mean": result.importances_mean,
            "auc_drop_std": result.importances_std,
        }
    ).sort_values("auc_drop_mean", ascending=False, ignore_index=True)
