from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ml.features import TARGET
from ml.train import train


@pytest.fixture(scope="session")
def artifacts_dir(tmp_path_factory):
    """Real artifacts from ml.train on a small population with a planted signal (bureau score protects)."""
    rng = np.random.default_rng(3)
    n = 2500
    income = rng.lognormal(8.2, 0.5, n)
    installment = rng.lognormal(6, 0.5, n)
    frame = pd.DataFrame(
        {
            "principal_amount": rng.lognormal(9, 0.6, n),
            "installments_count": rng.choice([6, 12, 24, 48], n),
            "installment_amount": installment,
            "installment_to_income_ratio": installment / income,
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
    target = tmp_path_factory.mktemp("ml_artifacts")
    train(frame, target)
    return target


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
                    # day 0: high but a partial window (not counted); day 8: high on a full window
                    "quarantine_pct_7d": 6.0 if d in (days[0], days[8]) else 1.2,
                    "dates_in_window_7d": min(i + 1, 7),
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
            [{"year_month": m, "product_type": p, "labeled": 35, "defaults": 4} for m in months for p in products]
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
