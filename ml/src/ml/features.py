"""Feature allowlist: the only columns the model may use, all known at the grant date.

`check_feature_list` rejects forbidden (outcome, key, lineage) and non-allowlisted columns; see
docs/DECISIONS.md (D22, D23).
"""

from __future__ import annotations

import pandas as pd

TARGET = "pd_label"

# Contract terms (fct_credit_operations / dim_products) known when the credit is granted.
_CONTRACT_NUMERIC = (
    "principal_amount",
    "installments_count",
    "installment_amount",
    "installment_to_income_ratio",
    "bureau_score_at_origination",
)
# Customer attributes at onboarding (dim_customers), incl. the pre-application Open Finance history.
_CUSTOMER_NUMERIC = (
    "age_at_registration",
    "monthly_income",
    "of_tx_count",
    "of_salary_deposit_count",
    "of_monthly_inflow",
    "of_monthly_outflow",
    "of_inflow_to_income_ratio",
    "of_betting_spend_share",
    "of_cash_withdrawal_share",
)

NUMERIC_FEATURES: tuple[str, ...] = _CONTRACT_NUMERIC + _CUSTOMER_NUMERIC
CATEGORICAL_FEATURES: tuple[str, ...] = ("product_type", "employment_type")
FEATURES: tuple[str, ...] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

# Right-skewed BRL amounts: log1p before scaling.
MONEY_FEATURES: tuple[str, ...] = (
    "principal_amount",
    "installment_amount",
    "monthly_income",
    "of_monthly_inflow",
    "of_monthly_outflow",
)

# Known at the grant date but left out: the bank's own risk-based price (feedback loop) and
# socio-economic / geographic proxies.
EXCLUDED_ON_PURPOSE: frozenset[str] = frozenset(
    {"interest_rate_monthly", "email_domain", "phone_area_code", "state_code"}
)

FORBIDDEN_COLUMNS: frozenset[str] = frozenset(
    {
        # outcome of the contract, observed after the grant
        "max_dpd_in_window",
        "has_defaulted_to_date",
        "is_mature",
        "window_installments",
        "window_installments_present",
        "is_window_complete",
        "window_last_due_date",
        "label_status",
        "days_past_due",
        "dpd_bucket",
        "amount_paid",
        "amount_overdue",
        TARGET,
        # identifiers, keys and lineage: no predictive meaning, only leakage risk
        "operation_id",
        "customer_id",
        "customer_key",
        "product_key",
        "contract_date_key",
        "batch_id",
        "observed_at",
    }
)


class LeakageError(ValueError):
    """A feature list contains a forbidden or non-allowlisted column."""


def check_feature_list(columns) -> None:
    columns = list(columns)
    forbidden = sorted(set(columns) & FORBIDDEN_COLUMNS)
    if forbidden:
        raise LeakageError(f"forbidden columns in the feature set: {forbidden}")
    unknown = sorted(set(columns) - set(FEATURES))
    if unknown:
        raise LeakageError(f"columns not in the allowlist: {unknown} (edit ml/features.py deliberately)")


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    """Exactly the allowlisted columns, in a fixed order; anything else in `frame` is ignored."""
    check_feature_list(FEATURES)
    return frame.loc[:, list(FEATURES)].copy()
