"""Out-of-time split: never random.

Only contracts up to `max_contract_date` (every product mature) are eligible. Train on contracts before
`test_start`, test on [`test_start`, `max_contract_date`]. Rows after the limit mean the population query
is wrong, so they raise instead of being silently dropped.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from ml.features import TARGET

TEST_START = date(2025, 4, 1)
MAX_CONTRACT_DATE = date(2025, 7, 17)


def out_of_time_split(
    frame: pd.DataFrame,
    date_column: str = "contract_date",
    test_start: date = TEST_START,
    max_contract_date: date = MAX_CONTRACT_DATE,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.to_datetime(frame[date_column]).dt.date
    if (dates > max_contract_date).any():
        raise ValueError(f"contracts after {max_contract_date} are not mature for every product")

    train = frame.loc[dates < test_start]
    test = frame.loc[dates >= test_start]
    for name, part in (("train", train), ("test", test)):
        if part.empty or part[TARGET].nunique() < 2:
            raise ValueError(f"{name} period needs both defaults and non-defaults")
    return train, test
