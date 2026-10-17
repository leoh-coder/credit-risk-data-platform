"""Training population from the marts. The SELECT list is generated from the allowlist, so outcome
columns are never fetched."""

from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

from ml.features import CATEGORICAL_FEATURES, FEATURES, TARGET, check_feature_list
from ml.split import MAX_CONTRACT_DATE

# Mart alias each allowlisted feature comes from.
FEATURE_SOURCE = {
    "principal_amount": "f",
    "installments_count": "f",
    "installment_amount": "f",
    "installment_to_income_ratio": "f",
    "bureau_score_at_origination": "f",
    "product_type": "p",
    "employment_type": "c",
    "age_at_registration": "c",
    "monthly_income": "c",
    "of_tx_count": "c",
    "of_salary_deposit_count": "c",
    "of_monthly_inflow": "c",
    "of_monthly_outflow": "c",
    "of_inflow_to_income_ratio": "c",
    "of_betting_spend_share": "c",
    "of_cash_withdrawal_share": "c",
}


def population_sql() -> str:
    check_feature_list(FEATURE_SOURCE)
    assert set(FEATURE_SOURCE) == set(FEATURES), "every allowlisted feature needs a source"
    # double precision, so pandas receives floats instead of Decimal
    columns = ",\n    ".join(
        f"{FEATURE_SOURCE[name]}.{name}"
        if name in CATEGORICAL_FEATURES
        else f"cast({FEATURE_SOURCE[name]}.{name} as double precision) as {name}"
        for name in FEATURES
    )
    return f"""
select
    f.operation_id,
    d.calendar_date as contract_date,
    f.{TARGET},
    {columns}
from marts.fct_credit_operations as f
inner join marts.dim_customers as c using (customer_key)
inner join marts.dim_products as p using (product_key)
inner join marts.dim_date as d on d.date_key = f.contract_date_key
where f.label_status = 'labeled'
  and not c.is_unknown_member
  and d.calendar_date <= :max_contract_date
"""


def load_population(database_url, max_contract_date: date = MAX_CONTRACT_DATE) -> pd.DataFrame:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return pd.read_sql(
                text(population_sql()), connection, params={"max_contract_date": max_contract_date}
            )
    finally:
        engine.dispose()
