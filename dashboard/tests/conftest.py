import json
import shutil
from datetime import date, datetime, timedelta

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from ml.features import FEATURES, TARGET, reference_installment_to_income_ratio
from ml.train import train


@pytest.fixture(scope="session")
def artifacts_dir(tmp_path_factory):
    """Real artifacts from ml.train on a small population with a planted signal (bureau score protects)."""
    rng = np.random.default_rng(3)
    n = 2500
    income = rng.lognormal(8.2, 0.5, n)
    principal = rng.lognormal(9, 0.6, n)
    installments = rng.choice([6, 12, 24, 48], n)
    frame = pd.DataFrame(
        {
            "principal_amount": principal,
            "installments_count": installments,
            "reference_installment_to_income_ratio": [
                reference_installment_to_income_ratio(p, k, i)
                for p, k, i in zip(principal, installments, income, strict=True)
            ],
            "bureau_score_at_origination": rng.integers(200, 1000, n),
            "age_at_registration": rng.integers(18, 80, n),
            "monthly_income": income,
            "of_tx_count": rng.integers(10, 90, n),
            "of_salary_deposit_count": rng.integers(0, 6, n),
            "of_monthly_inflow": rng.lognormal(8, 0.6, n),
            "of_monthly_outflow": rng.lognormal(8, 0.6, n),
            "of_inflow_to_income_ratio": rng.uniform(0.3, 3, n),
            "of_betting_spend_share": rng.beta(1, 12, n),
            "of_cash_withdrawal_share": rng.beta(2, 15, n),
            "product_type": rng.choice(["personal_loan", "payroll_loan", "vehicle_financing"], n),
            "employment_type": rng.choice(["clt", "public_servant", "self_employed"], n),
        }
    )
    logit = -1.8 - 0.008 * (frame["bureau_score_at_origination"] - 600)
    frame[TARGET] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    frame["contract_date"] = pd.to_datetime("2024-07-01") + pd.to_timedelta(rng.integers(0, 382, n), unit="D")
    frame["operation_id"] = [f"OPR{i:07d}" for i in range(n)]
    frame["observed_at"] = pd.Timestamp("2026-10-15 23:59:59")
    target = tmp_path_factory.mktemp("ml_artifacts")
    train(frame, target)
    return target


@pytest.fixture
def outdated_artifacts_dir(artifacts_dir, tmp_path):
    """Artifacts left in the ml-artifacts volume by a model trained on an older feature list (v1.0.0)."""
    outdated = tmp_path / "outdated_artifacts"
    shutil.copytree(artifacts_dir, outdated)
    old = Pipeline(
        [("prep", ColumnTransformer([("num", "passthrough", ["installment_amount"])])), ("model", LogisticRegression())]
    )
    old.fit(pd.DataFrame({"installment_amount": [100.0, 200.0, 300.0, 400.0]}), [0, 0, 1, 1])
    joblib.dump(old, outdated / "model.joblib")
    metrics = json.loads((outdated / "metrics.json").read_text())
    metrics["features"] = [f for f in FEATURES if f != "reference_installment_to_income_ratio"] + [
        "installment_amount",
        "installment_to_income_ratio",
    ]
    metrics["split"].pop("validation")  # v1.0.0 had no validation period
    for model_metrics in metrics["models"].values():
        model_metrics.pop("validation")
    (outdated / "metrics.json").write_text(json.dumps(metrics))
    return outdated


def fake_marts() -> dict[str, pd.DataFrame]:
    """One small frame per dashboard query, shaped like the marts."""
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(10)]
    entities = ["customers", "credit_operations", "installments", "transactions"]
    products = ["payroll_loan", "personal_loan", "vehicle_financing"]
    months = ["2025-01", "2025-02", "2025-03"]
    return {
        "pipeline_freshness": pd.DataFrame(
            {
                "last_load_at": [datetime(2026, 10, 16, 12)],
                "latest_reference_date": [days[-1]],
                "dates_loaded": [10],
                "loads": [12],
            }
        ),
        "pipeline_loads": pd.DataFrame(
            {"reference_date": days, "rows_loaded": [3000] * 10, "load_seconds": [0.4] * 10, "finished_at": days}
        ),
        "ingestion_quality": pd.DataFrame(
            [
                {
                    "reference_date": d,
                    "entity": e,
                    "rows_received": 100,
                    "rows_quarantined": 1,
                    "quarantine_pct": 1.0,
                    "quarantine_pct_7d": 6.0 if d in (days[0], days[8]) else 1.2,
                    "dates_in_window_7d": min(i + 1, 7),
                    # budgets and flags come from the warehouse; the dashboard must not re-derive them
                    "quarantine_budget_warn_pct": 5.5,
                    "quarantine_budget_error_pct": 9.0,
                    "is_over_warn_budget": d == days[8] and e == "customers",
                    "is_over_error_budget": False,
                }
                for i, d in enumerate(days)
                for e in entities
            ]
        ),
        "portfolio_monthly": pd.DataFrame(
            [
                {"year_month": m, "product_type": p, "contracts": 40, "principal_amount": 4e5}
                for m in months
                for p in products
            ]
        ),
        "cohort_default": pd.DataFrame(
            [
                {"year_month": m, "product_type": p, "labeled": 35, "defaults": 4, "immature": 0}
                for m in months
                for p in products
            ]
            # a recent cohort: only short personal loans are labeled, the other products are immature
            + [
                {
                    "year_month": "2025-08",
                    "product_type": "personal_loan",
                    "labeled": 35,
                    "defaults": 20,
                    "immature": 0,
                },
                {"year_month": "2025-08", "product_type": "payroll_loan", "labeled": 0, "defaults": 0, "immature": 40},
                # a partial personal-loan cohort: the short loans are labeled, the longer ones not yet
                {
                    "year_month": "2025-09",
                    "product_type": "personal_loan",
                    "labeled": 40,
                    "defaults": 30,
                    "immature": 10,
                },
            ]
        ),
        "label_status": pd.DataFrame(
            {"label_status": ["immature", "incomplete_schedule", "labeled"], "contracts": [50, 5, 105]}
        ),
        "delinquency_aging": pd.DataFrame(
            [
                {"product_type": p, "dpd_bucket": b, "installments": 3, "amount_overdue": 900.0}
                for p in products
                for b in ["1-30", "90+"]
            ]
        ),
    }
