"""Model artifacts and individual scoring for the risk simulation tab."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd
from sklearn.pipeline import Pipeline

from ml.explain import contributions
from ml.features import (
    DERIVED_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    feature_matrix,
    reference_installment_to_income_ratio,
)

# Display-only grades for the simulated PD (not a credit policy): each band's exclusive upper bound, then
# the top band for everything above the last bound.
RISK_BANDS: tuple[tuple[str, float], ...] = (("A", 0.02), ("B", 0.05), ("C", 0.10), ("D", 0.20))
TOP_RISK_BAND = "E"


# Optional display metadata (label, min, max, step); integer steps give integer inputs. A numeric feature
# without an entry still gets a generic field, so adding a feature never breaks the simulation.
FIELD_META: dict[str, tuple[str, float, float, float]] = {
    "principal_amount": ("Principal (BRL)", 500.0, 500_000.0, 500.0),
    "installments_count": ("Installments", 1, 120, 1),
    "bureau_score_at_origination": ("Bureau score", 0, 1000, 10),
    "age_at_registration": ("Age", 18, 100, 1),
    "monthly_income": ("Monthly income (BRL)", 0.0, 200_000.0, 100.0),
    "of_tx_count": ("Open Finance transactions (90 d)", 0, 2000, 1),
    "of_salary_deposit_count": ("Salary deposits (90 d)", 0, 20, 1),
    "of_monthly_inflow": ("Monthly inflow (BRL)", 0.0, 500_000.0, 100.0),
    "of_monthly_outflow": ("Monthly outflow (BRL)", 0.0, 500_000.0, 100.0),
    "of_inflow_to_income_ratio": ("Inflow / declared income", 0.0, 20.0, 0.05),
    "of_betting_spend_share": ("Betting share of spend", 0.0, 1.0, 0.01),
    "of_cash_withdrawal_share": ("Cash withdrawal share of spend", 0.0, 1.0, 0.01),
}


@dataclass(frozen=True)
class SimulationField:
    feature: str
    label: str
    min_value: float
    max_value: float
    step: float


def simulation_fields(
    numeric_features: tuple[str, ...] = NUMERIC_FEATURES, derived: dict = DERIVED_FEATURES
) -> list[SimulationField]:
    """One typed input per numeric model feature that is not derived from other inputs."""
    fields = []
    for feature in numeric_features:
        if feature in derived:
            continue
        label, low, high, step = FIELD_META.get(feature, (feature.replace("_", " ").capitalize(), 0.0, 1e9, 1.0))
        fields.append(SimulationField(feature, label, low, high, step))
    return fields


@dataclass(frozen=True)
class ModelArtifacts:
    model: Pipeline
    metrics: dict
    simulation_defaults: dict
    coefficients: pd.DataFrame
    permutation_importance: pd.DataFrame
    calibration: pd.DataFrame

    @property
    def is_current(self) -> bool:
        """False when the artifacts come from a model trained on another feature list (an older volume)."""
        return self.metrics.get("features") == list(FEATURES)


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
    """One applicant as the model expects it; derived features are computed as in fct_credit_operations."""
    row = dict(values)
    row["reference_installment_to_income_ratio"] = reference_installment_to_income_ratio(
        row["principal_amount"], row["installments_count"], row.get("monthly_income") or 0
    )
    return feature_matrix(pd.DataFrame([row]))


def risk_band(pd_hat: float) -> str:
    return next((band for band, upper in RISK_BANDS if pd_hat < upper), TOP_RISK_BAND)


def score(model: Pipeline, values: dict) -> tuple[float, pd.Series]:
    """PD and its exact log-odds decomposition (intercept + one term per feature)."""
    row = applicant_frame(values)
    pd_hat = float(model.predict_proba(row)[:, 1][0])
    return pd_hat, contributions(model, row)


def readable_input(name: str, feature: str) -> str:
    """Label for a model input: 'cat__product_type_payroll_loan' -> 'product_type = payroll_loan', and money
    inputs, which the model sees as log1p, -> 'monthly_income (log)'."""
    block, _, column = name.partition("__")
    if block == "cat":
        level = column.removeprefix(f"{feature}_")
        return f"{feature} = {'other (infrequent)' if level == 'infrequent_sklearn' else level}"
    return f"{feature} (log)" if block == "money" else feature
