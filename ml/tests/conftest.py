import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURES, TARGET


@pytest.fixture(scope="session")
def synthetic_population() -> pd.DataFrame:
    """Marts-shaped labeled population with a known signal: bureau score protects, betting share and
    personal loans raise the risk; every other feature is noise."""
    rng = np.random.default_rng(7)
    n = 4000
    frame = pd.DataFrame(
        {
            "principal_amount": rng.lognormal(9, 0.6, n),
            "installments_count": rng.choice([6, 12, 24, 48], n),
            "installment_amount": rng.lognormal(6, 0.5, n),
            "installment_to_income_ratio": rng.uniform(0.02, 0.4, n),
            "bureau_score_at_origination": rng.integers(200, 1000, n),
            "age_at_registration": rng.integers(18, 80, n),
            "monthly_income": rng.lognormal(8.2, 0.5, n),
            "of_tx_count": rng.integers(10, 90, n),
            "of_salary_deposit_count": rng.integers(0, 6, n),
            "of_monthly_inflow": rng.lognormal(8, 0.6, n),
            "of_monthly_outflow": rng.lognormal(8, 0.6, n),
            "of_inflow_to_income_ratio": rng.uniform(0.3, 3, n),
            "of_betting_spend_share": rng.beta(1, 12, n),
            "of_cash_withdrawal_share": rng.beta(2, 15, n),
            "product_type": rng.choice(
                ["personal_loan", "payroll_loan", "vehicle_financing"], n, p=[0.6, 0.2, 0.2]
            ),
            "employment_type": rng.choice(
                ["clt", "public_servant", "self_employed", "retired", "informal"], n
            ),
            "state_code": rng.choice(["SP", "MG", "RJ", "BA", "PR"], n),
        }
    )
    logit = (
        -1.5
        - 0.006 * (frame["bureau_score_at_origination"] - 600)
        + 12 * frame["of_betting_spend_share"]
        + 0.9 * (frame["product_type"] == "personal_loan")
    )
    frame[TARGET] = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    frame["contract_date"] = pd.to_datetime("2024-07-01") + pd.to_timedelta(rng.integers(0, 382, n), unit="D")
    frame["operation_id"] = [f"OPR{i:07d}" for i in range(n)]
    frame["max_dpd_in_window"] = np.where(
        frame[TARGET] == 1, 120, 0
    )  # outcome column the model must never see
    assert set(FEATURES) <= set(frame.columns)
    return frame
