"""Train the PD model out of time and write its artifacts.

    python -m ml.train          # reads the marts as ml_reader, writes to ARTIFACTS_DIR

Artifacts: model.joblib (final logistic pipeline), metrics.json, coefficients.csv, permutation_importance.csv
(validation period), calibration.csv (test period), simulation_defaults.json, model_card.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pandas as pd
import sklearn
from sklearn.pipeline import Pipeline

from ml.config import get_settings
from ml.data import load_population
from ml.explain import odds_ratios, permutation_importances
from ml.features import (
    CATEGORICAL_FEATURES,
    EXCLUDED_ON_PURPOSE,
    FEATURES,
    FORBIDDEN_COLUMNS,
    NUMERIC_FEATURES,
    PRICE_DEPENDENT_COLUMNS,
    REFERENCE_CATEGORIES,
    TARGET,
    feature_matrix,
)
from ml.metrics import calibration_table, evaluate
from ml.model import gradient_boosting_pipeline, logistic_pipeline
from ml.split import max_mature_contract_date, out_of_time_split

logger = logging.getLogger("ml")


def _period(frame: pd.DataFrame) -> dict:
    dates = pd.to_datetime(frame["contract_date"])
    return {"from": str(dates.min().date()), "to": str(dates.max().date()), **evaluate_counts(frame)}


def evaluate_counts(frame: pd.DataFrame) -> dict:
    return {"rows": len(frame), "defaults": int(frame[TARGET].sum())}


# Candidates are compared on the validation period only; the test period is scored once, at the end.
LOGISTIC_GRID = ({"C": 0.1}, {"C": 1.0}, {"C": 10.0})
BOOSTING_GRID = (
    {"max_depth": 2, "min_samples_leaf": 200},
    {"max_depth": 3, "min_samples_leaf": 100},
    {"max_depth": 4, "min_samples_leaf": 50},
)


def select(build, grid, X_train, y_train, X_validation, y_validation) -> tuple[dict, Pipeline, list[dict]]:
    """Fit every candidate on the training period and keep the best validation AUC (first wins ties)."""
    candidates, best = [], None
    for params in grid:
        model = build(**params).fit(X_train, y_train)
        auc = evaluate(y_validation, model.predict_proba(X_validation)[:, 1])["auc"]
        candidates.append({"params": params, "validation_auc": auc})
        if best is None or auc > best[2]:
            best = (params, model, auc)
    return best[0], best[1], candidates


def _max_decile_gap(y, pd_hat) -> float:
    table = calibration_table(y, pd_hat)
    return float((table["mean_pd"] - table["observed_rate"]).abs().max())


def train(
    population: pd.DataFrame, artifacts_dir: Path, validation_months: int = 3, test_months: int = 3
) -> dict:
    observation_date = pd.to_datetime(population["observed_at"]).min().date()
    max_contract_date = max_mature_contract_date(observation_date)
    # After the limit only short products are mature: their mix would bias the rates, so they are reported.
    is_mature = pd.to_datetime(population["contract_date"]).dt.date <= max_contract_date
    mature, excluded = population.loc[is_mature], evaluate_counts(population.loc[~is_mature])
    logger.info(
        "excluded %d contracts (%d defaults) after the maturity limit %s",
        excluded["rows"],
        excluded["defaults"],
        max_contract_date,
    )
    train_df, validation_df, test_df = out_of_time_split(
        mature, max_contract_date, validation_months=validation_months, test_months=test_months
    )
    X_train, y_train = feature_matrix(train_df), train_df[TARGET]
    X_validation, y_validation = feature_matrix(validation_df), validation_df[TARGET]
    X_test, y_test = feature_matrix(test_df), test_df[TARGET]
    X_final, y_final = pd.concat([X_train, X_validation]), pd.concat([y_train, y_validation])

    models, fitted = {}, {}
    for name, build, grid in (
        ("logistic_regression", logistic_pipeline, LOGISTIC_GRID),
        ("hist_gradient_boosting", gradient_boosting_pipeline, BOOSTING_GRID),
    ):
        params, selected, candidates = select(build, grid, X_train, y_train, X_validation, y_validation)
        final = build(**params).fit(X_final, y_final)
        pd_test = final.predict_proba(X_test)[:, 1]
        fitted[name] = (selected, final, pd_test)
        models[name] = {
            "params": params,
            "candidates": candidates,
            "train": evaluate(y_train, selected.predict_proba(X_train)[:, 1]),
            "validation": evaluate(y_validation, selected.predict_proba(X_validation)[:, 1]),
            "test": evaluate(y_test, pd_test),
            "test_max_decile_gap": _max_decile_gap(y_test, pd_test),
            "final_fit_rows": len(X_final),
        }

    selected_logistic, logistic, pd_test = fitted["logistic_regression"]
    models["logistic_regression"]["test_by_product"] = {
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
            "observation_date": str(observation_date),
            "max_contract_date": str(max_contract_date),
            "excluded_after_maturity_limit": excluded,
            "validation_months": validation_months,
            "test_months": test_months,
            "train": _period(train_df),
            "validation": _period(validation_df),
            "test": _period(test_df),
        },
        "features": list(FEATURES),
        "forbidden_columns": sorted(FORBIDDEN_COLUMNS),
        "models": models,
    }

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    joblib.dump(logistic, artifacts_dir / "model.joblib")
    (artifacts_dir / "metrics.json").write_text(json.dumps(report, indent=2))
    odds_ratios(logistic).to_csv(artifacts_dir / "coefficients.csv", index=False)
    permutation_importances(selected_logistic, validation_df, y_validation).to_csv(
        artifacts_dir / "permutation_importance.csv", index=False
    )
    calibration_table(y_test, pd_test).to_csv(artifacts_dir / "calibration.csv", index=False)
    (artifacts_dir / "simulation_defaults.json").write_text(
        json.dumps(simulation_defaults(X_final), indent=2)
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

    excluded = split["excluded_after_maturity_limit"]
    late = f"{excluded['rows']} contracts ({excluded['defaults']} defaults)"
    products = "\n".join(row(product, m) for product, m in lr["test_by_product"].items())
    features = ", ".join(f"`{f}`" for f in report["features"])
    forbidden = ", ".join(f"`{f}`" for f in report["forbidden_columns"])
    priced = ", ".join(f"`{c}`" for c in sorted(PRICE_DEPENDENT_COLUMNS))
    proxies = ", ".join(f"`{c}`" for c in sorted(EXCLUDED_ON_PURPOSE - PRICE_DEPENDENT_COLUMNS))
    references = ", ".join(f"`{f}` = `{c}`" for f, c in REFERENCE_CATEGORIES.items())
    return f"""# Model card: probability of default (PD)

- **Trained at:** {report["trained_at"]} (scikit-learn {report["scikit_learn"]})
- **Target:** `pd_label` = 1 when DPD >= 90 within the first 12 installments (mature contracts only).
- **Population:** {report["population"]}.
- **Validation:** out of time, never random.
  - Train: {period(split["train"])}.
  - Validation: {period(split["validation"])}.
  - Test: {period(split["test"])}.
  - Observation clock {split["observation_date"]}: the {late} after {split["max_contract_date"]} are
    excluded and reported, because after that date only short products are mature and their mix would
    bias the rates.
- **Selection:** hyperparameters and permutation importance use the validation period only. The chosen
  models are refit on train + validation and scored once on the test period.
- **Main model:** logistic regression (the standard in credit scoring), {lr["params"]}. Comparison:
  HistGradientBoosting, {hgb["params"]}.

## Metrics

| Model / period | AUC | Gini | KS | Brier | Mean PD | Observed rate |
|---|---|---|---|---|---|---|
{row("Logistic, train", lr["train"])}
{row("Logistic, validation", lr["validation"])}
{row("Logistic, test (out of time, final model)", lr["test"])}
{row("HistGradientBoosting, train", hgb["train"])}
{row("HistGradientBoosting, validation", hgb["validation"])}
{row("HistGradientBoosting, test (out of time, final model)", hgb["test"])}

Largest gap between predicted and observed default rate across test deciles: logistic
{lr["test_max_decile_gap"]:.3f}, HistGradientBoosting {hgb["test_max_decile_gap"]:.3f}.

Test by product (logistic):

| Product | AUC | Gini | KS | Brier | Mean PD | Observed rate |
|---|---|---|---|---|---|---|
{products}

## Features (allowlist, known at the grant date)

{features}

Categories are coded against a reference category ({references}); their odds ratios read
"vs. the reference category".

Never used (outcome, identifier or lineage columns): {forbidden}.
Excluded on purpose, although known at the grant date: {priced} (they embed the bank's risk-based
price; affordability uses the installment at a fixed reference rate instead) and {proxies}
(socio-economic / geographic proxies).

## Limitations

- Synthetic data: the hidden risk driver is never exported, so the ceiling AUC is unknown and below 1.
- Contracts whose schedule lost an installment in quarantine are never labeled (`incomplete_schedule`),
  even when a default was observed in them.
- `age_at_registration` is sensitive in credit decisions (age discrimination); review fairness before
  any real use.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ml.train", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    settings = get_settings()
    population = load_population(settings.database_url)
    logger.info("loaded %d labeled contracts (all cohorts)", len(population))
    report = train(
        population,
        Path(settings.artifacts_dir),
        validation_months=settings.validation_months,
        test_months=settings.test_months,
    )
    split = report["split"]
    for period in ("train", "validation", "test"):
        p = split[period]
        logger.info(
            "%-10s %s..%s %d contracts, %d defaults", period, p["from"], p["to"], p["rows"], p["defaults"]
        )
    test = report["models"]["logistic_regression"]["test"]
    logger.info("out-of-time test: AUC %.3f | Gini %.3f | KS %.3f", test["auc"], test["gini"], test["ks"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
