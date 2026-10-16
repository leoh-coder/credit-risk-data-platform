"""Train the PD model out of time and write its artifacts.

    python -m ml.train          # reads the marts as ml_reader, writes to ARTIFACTS_DIR

Artifacts: model.joblib (logistic pipeline), metrics.json, coefficients.csv, permutation_importance.csv,
calibration.csv, simulation_defaults.json, model_card.md.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd
import sklearn

from ml.config import get_settings
from ml.data import load_population
from ml.explain import odds_ratios, permutation_importances
from ml.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    FORBIDDEN_COLUMNS,
    NUMERIC_FEATURES,
    TARGET,
    feature_matrix,
)
from ml.metrics import calibration_table, evaluate
from ml.model import gradient_boosting_pipeline, logistic_pipeline
from ml.split import MAX_CONTRACT_DATE, TEST_START, out_of_time_split

logger = logging.getLogger("ml")


def _period(frame: pd.DataFrame) -> dict:
    dates = pd.to_datetime(frame["contract_date"])
    return {"from": str(dates.min().date()), "to": str(dates.max().date()), **evaluate_counts(frame)}


def evaluate_counts(frame: pd.DataFrame) -> dict:
    return {"rows": int(len(frame)), "defaults": int(frame[TARGET].sum())}


def train(population: pd.DataFrame, artifacts_dir: Path) -> dict:
    train_df, test_df = out_of_time_split(population)
    X_train, y_train = feature_matrix(train_df), train_df[TARGET]
    X_test, y_test = feature_matrix(test_df), test_df[TARGET]

    logistic = logistic_pipeline().fit(X_train, y_train)
    boosting = gradient_boosting_pipeline().fit(X_train, y_train)
    pd_test = logistic.predict_proba(X_test)[:, 1]

    by_product = {
        product: evaluate(y_test[mask], pd_test[mask.to_numpy()])
        for product, mask in (
            (p, test_df["product_type"] == p) for p in sorted(test_df["product_type"].unique())
        )
        if y_test[mask].nunique() == 2
    }
    report = {
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "scikit_learn": sklearn.__version__,
        "population": "fct_credit_operations x dim_customers, label_status = 'labeled', known customers",
        "split": {
            "type": "out_of_time",
            "test_start": str(TEST_START),
            "max_contract_date": str(MAX_CONTRACT_DATE),
            "train": _period(train_df),
            "test": _period(test_df),
        },
        "features": list(FEATURES),
        "forbidden_columns": sorted(FORBIDDEN_COLUMNS),
        "models": {
            "logistic_regression": {
                "train": evaluate(y_train, logistic.predict_proba(X_train)[:, 1]),
                "test": evaluate(y_test, pd_test),
                "test_by_product": by_product,
            },
            "hist_gradient_boosting": {
                "train": evaluate(y_train, boosting.predict_proba(X_train)[:, 1]),
                "test": evaluate(y_test, boosting.predict_proba(X_test)[:, 1]),
            },
        },
    }

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(logistic, artifacts_dir / "model.joblib")
    (artifacts_dir / "metrics.json").write_text(json.dumps(report, indent=2))
    odds_ratios(logistic).to_csv(artifacts_dir / "coefficients.csv", index=False)
    permutation_importances(logistic, test_df, y_test).to_csv(
        artifacts_dir / "permutation_importance.csv", index=False
    )
    calibration_table(y_test, pd_test).to_csv(artifacts_dir / "calibration.csv", index=False)
    (artifacts_dir / "simulation_defaults.json").write_text(
        json.dumps(simulation_defaults(X_train), indent=2)
    )
    (artifacts_dir / "model_card.md").write_text(model_card(report))
    return report


def simulation_defaults(X_train: pd.DataFrame) -> dict:
    """Starting values for the dashboard's simulation form: training medians, modes and seen categories."""
    return {
        "numeric": {c: float(X_train[c].median()) for c in NUMERIC_FEATURES},
        "categorical": {
            c: {
                "default": str(X_train[c].mode().iat[0]),
                "options": sorted(map(str, X_train[c].dropna().unique())),
            }
            for c in CATEGORICAL_FEATURES
        },
    }


def model_card(report: dict) -> str:
    split, lr = report["split"], report["models"]["logistic_regression"]
    hgb = report["models"]["hist_gradient_boosting"]

    def row(name, m):
        values = [m["auc"], m["gini"], m["ks"], m["mean_pd"], m["observed_default_rate"]]
        cells = [f"{v:.3f}" for v in values[:3]] + [f"{m['brier']:.4f}"] + [f"{v:.3f}" for v in values[3:]]
        return f"| {name} | " + " | ".join(cells) + " |"

    def period(p):
        return f"{p['from']} .. {p['to']} ({p['rows']} contracts, {p['defaults']} defaults)"

    products = "\n".join(row(product, m) for product, m in lr["test_by_product"].items())
    features = ", ".join(f"`{f}`" for f in report["features"])
    forbidden = ", ".join(f"`{f}`" for f in report["forbidden_columns"])
    return f"""# Model card: probability of default (PD)

- **Trained at:** {report["trained_at"]} (scikit-learn {report["scikit_learn"]})
- **Target:** `pd_label` = 1 when DPD >= 90 within the first 12 installments (mature contracts only).
- **Population:** {report["population"]}.
- **Validation:** out of time, never random.
  - Train: {period(split["train"])}.
  - Test: {period(split["test"])}.
  - Contracts after {split["max_contract_date"]} are excluded: not every product is mature after that date.
- **Main model:** logistic regression (scorecard standard). Comparison: HistGradientBoosting.

## Metrics

| Model / period | AUC | Gini | KS | Brier | Mean PD | Observed rate |
|---|---|---|---|---|---|---|
{row("Logistic, train", lr["train"])}
{row("Logistic, test (out of time)", lr["test"])}
{row("HistGradientBoosting, test", hgb["test"])}

Test by product (logistic):

| Product | AUC | Gini | KS | Brier | Mean PD | Observed rate |
|---|---|---|---|---|---|---|
{products}

## Features (allowlist, known at the grant date)

{features}

Never used (outcome, identifier or lineage columns): {forbidden}.
Excluded on purpose: `interest_rate_monthly` (output of the bank's own pricing: feedback loop),
`email_domain`, `phone_area_code` and `state_code` (socio-economic / geographic proxies).

## Limitations

- Synthetic data: the hidden risk driver is never exported, so the ceiling AUC is unknown and below 1.
- Contracts whose schedule lost an installment in quarantine are not labeled as goods
  (`incomplete_schedule`).
- `age_at_registration` is sensitive in credit decisions (age discrimination); review fairness before
  any real use.
"""


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    settings = get_settings()
    population = load_population(settings.database_url)
    logger.info(
        "population: %d labeled contracts, %d defaults", len(population), int(population[TARGET].sum())
    )
    report = train(population, Path(settings.artifacts_dir))
    test = report["models"]["logistic_regression"]["test"]
    logger.info("out-of-time test: AUC %.3f | Gini %.3f | KS %.3f", test["auc"], test["gini"], test["ks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
