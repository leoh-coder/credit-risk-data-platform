"""Out-of-time split on contract dates, bounded by the last contract that is mature for every product.

Contracts after that date are only mature for short products; they raise instead of being dropped.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ml.features import TARGET

# Must match the dbt vars pd_window_installments and pd_default_dpd (tested).
DEFAULT_WINDOW_INSTALLMENTS = 12
DEFAULT_DPD = 90


def max_mature_contract_date(
    observation_date: date,
    window_installments: int = DEFAULT_WINDOW_INSTALLMENTS,
    default_dpd: int = DEFAULT_DPD,
) -> date:
    """Last contract date whose full label window is mature at the observation clock."""
    latest_due_date = pd.Timestamp(observation_date) - pd.Timedelta(days=default_dpd)
    return (latest_due_date - pd.DateOffset(months=window_installments)).date()


def out_of_time_split(
    frame: pd.DataFrame, max_contract_date: date, test_months: int = 3, date_column: str = "contract_date"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on contracts up to max - test_months, test on the last test_months up to max."""
    dates = pd.to_datetime(frame[date_column]).dt.date
    if (dates > max_contract_date).any():
        raise ValueError(f"contracts after {max_contract_date} are not mature for every product")

    test_start = (pd.Timestamp(max_contract_date) - pd.DateOffset(months=test_months)).date()
    train = frame.loc[dates <= test_start]
    test = frame.loc[dates > test_start]
    for name, part in (("train", train), ("test", test)):
        if part.empty or part[TARGET].nunique() < 2:
            raise ValueError(f"{name} period needs both defaults and non-defaults")
    return train, test
