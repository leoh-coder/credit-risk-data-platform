"""Model artifacts and individual scoring for the risk simulation tab."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from ml.explain import contributions
from ml.features import FEATURES, feature_matrix

# Display-only grades for the simulated PD (not a credit policy).
RISK_BANDS: tuple[tuple[str, float], ...] = (("A", 0.02), ("B", 0.05), ("C", 0.10), ("D", 0.20), ("E", 1.01))


@dataclass(frozen=True)
class ModelArtifacts:
    model: Pipeline
    metrics: dict
    simulation_defaults: dict
    coefficients: pd.DataFrame
    permutation_importance: pd.DataFrame
    calibration: pd.DataFrame


def load_artifacts(artifacts_dir: str | Path) -> ModelArtifacts | None:
    """None when the model has not been trained yet."""
    base = Path(artifacts_dir)
    if not (base / "model.joblib").exists():
        return None
    return ModelArtifacts(
        model=joblib.load(base / "model.joblib"),
        metrics=json.loads((base / "metrics.json").read_text()),
        simulation_defaults=json.loads((base / "simulation_defaults.json").read_text()),
        coefficients=pd.read_csv(base / "coefficients.csv"),
        permutation_importance=pd.read_csv(base / "permutation_importance.csv"),
        calibration=pd.read_csv(base / "calibration.csv"),
    )


def applicant_frame(values: dict) -> pd.DataFrame:
    """One applicant as the model expects it; the ratio is derived as in fct_credit_operations."""
    row = dict(values)
    income = row.get("monthly_income") or 0
    row["installment_to_income_ratio"] = row["installment_amount"] / income if income > 0 else np.nan
    return feature_matrix(pd.DataFrame([row]))


def risk_band(pd_hat: float) -> str:
    return next(band for band, upper in RISK_BANDS if pd_hat < upper)


def score(model: Pipeline, values: dict) -> tuple[float, pd.Series]:
    """PD and its exact log-odds decomposition (intercept + one term per feature)."""
    row = applicant_frame(values)
    pd_hat = float(model.predict_proba(row)[:, 1][0])
    return pd_hat, contributions(model, row)


__all__ = ["FEATURES", "RISK_BANDS", "ModelArtifacts", "applicant_frame", "load_artifacts", "risk_band", "score"]
