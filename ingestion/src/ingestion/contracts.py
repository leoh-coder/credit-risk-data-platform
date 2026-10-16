"""Data contracts for the raw layer, enforced with Pandera.

A data contract is the formal agreement between the producer (core banking) and
the consumers (warehouse, dbt, ML). It states column names, types, nullability
and business rules. Two kinds of violation are handled differently:

- Schema-level (missing/unexpected column, wrong table shape) -> HARD FAIL.
  The whole batch is rejected: the producer broke the contract and must bump
  the contract version. Loading it would silently corrupt every downstream model.
- Row-level (negative amount, invalid state, duplicated key...) -> SOFT FAIL.
  Bad rows go to a quarantine zone with the reason attached; good rows flow on.
  A circuit breaker still fails the batch if too many rows are bad.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import pandera.pandas as pa
from pandera.typing import Series

from ingestion.generator import (
    CHANNELS,
    EMPLOYMENT_TYPES,
    MERCHANT_CATEGORIES,
    PRODUCT_TYPES,
    STATES,
    TRANSACTION_TYPES,
)

CONTRACT_VERSION = "1.0.0"


class ContractViolationError(Exception):
    """Raised when a batch breaks the contract at schema level or exceeds the error budget."""


class _LineageColumns(pa.DataFrameModel):
    batch_id: Series[str] = pa.Field(alias="_batch_id", str_matches=r"^\d{8}-[0-9a-f]{8}$")
    reference_date: Series[pd.Timestamp] = pa.Field(alias="_reference_date")
    extracted_at: Series[pd.Timestamp] = pa.Field(alias="_extracted_at")
    generator_version: Series[str] = pa.Field(alias="_generator_version")

    class Config:
        coerce = True
        strict = True  # unknown columns = schema drift = hard fail


class CustomerContract(_LineageColumns):
    customer_id: Series[str] = pa.Field(unique=True, str_matches=r"^CUS\d{13}$")
    cpf: Series[str] = pa.Field(str_matches=r"^\d{3}\.\d{3}\.\d{3}-\d{2}$")
    full_name: Series[str] = pa.Field(str_length={"min_value": 3, "max_value": 200})
    email: Series[str] = pa.Field(str_matches=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone_number: Series[str]
    birth_date: Series[pd.Timestamp]
    state: Series[str] = pa.Field(isin=STATES.tolist())
    employment_type: Series[str] = pa.Field(isin=EMPLOYMENT_TYPES.tolist())
    monthly_income: Series[float] = pa.Field(gt=0, le=1_000_000)
    registration_date: Series[pd.Timestamp]

    @pa.dataframe_check(element_wise=False)
    def customer_is_adult(cls, df: pd.DataFrame) -> Series[bool]:
        """Credit can only be granted to customers aged 18+ at registration."""
        return df["birth_date"] <= df["registration_date"] - pd.DateOffset(years=18)


class CreditOperationContract(_LineageColumns):
    operation_id: Series[str] = pa.Field(unique=True, str_matches=r"^OPR\d{13}$")
    customer_id: Series[str] = pa.Field(str_matches=r"^CUS\d{13}$")
    product_type: Series[str] = pa.Field(isin=PRODUCT_TYPES.tolist())
    contract_date: Series[pd.Timestamp]
    principal_amount: Series[float] = pa.Field(gt=0, le=5_000_000)
    interest_rate_monthly: Series[float] = pa.Field(gt=0, le=0.15)
    installments_count: Series[int] = pa.Field(ge=1, le=120)
    installment_amount: Series[float] = pa.Field(gt=0)
    bureau_score_at_origination: Series[int] = pa.Field(ge=0, le=1000)


class InstallmentContract(_LineageColumns):
    installment_id: Series[str] = pa.Field(unique=True, str_matches=r"^OPR\d{13}-\d{3}$")
    operation_id: Series[str] = pa.Field(str_matches=r"^OPR\d{13}$")
    installment_number: Series[int] = pa.Field(ge=1, le=120)
    due_date: Series[pd.Timestamp]
    amount_due: Series[float] = pa.Field(gt=0)
    payment_date: Series[pd.Timestamp] = pa.Field(nullable=True)
    amount_paid: Series[float] = pa.Field(ge=0)

    @pa.dataframe_check(element_wise=False)
    def no_payment_after_extraction(cls, df: pd.DataFrame) -> Series[bool]:
        return df["payment_date"].isna() | (df["payment_date"] <= df["_extracted_at"])


class TransactionContract(_LineageColumns):
    transaction_id: Series[str] = pa.Field(unique=True, str_matches=r"^TRX\d{15}$")
    customer_id: Series[str] = pa.Field(str_matches=r"^CUS\d{13}$")
    transaction_ts: Series[pd.Timestamp]
    transaction_type: Series[str] = pa.Field(isin=TRANSACTION_TYPES.tolist())
    direction: Series[str] = pa.Field(isin=["credit", "debit"])
    amount: Series[float] = pa.Field(gt=0, le=1_000_000)
    merchant_category: Series[str] = pa.Field(isin=MERCHANT_CATEGORIES.tolist(), nullable=True)
    channel: Series[str] = pa.Field(isin=CHANNELS.tolist())

    @pa.dataframe_check(element_wise=False)
    def no_future_transactions(cls, df: pd.DataFrame) -> Series[bool]:
        return df["transaction_ts"] <= df["_extracted_at"]


CONTRACTS: dict[str, type[pa.DataFrameModel]] = {
    "customers": CustomerContract,
    "credit_operations": CreditOperationContract,
    "installments": InstallmentContract,
    "transactions": TransactionContract,
}


@dataclass
class ValidationResult:
    entity: str
    valid: pd.DataFrame
    quarantine: pd.DataFrame

    @property
    def invalid_ratio(self) -> float:
        total = len(self.valid) + len(self.quarantine)
        return len(self.quarantine) / total if total else 0.0


def validate_entity(entity: str, df: pd.DataFrame, max_invalid_ratio: float = 0.10) -> ValidationResult:
    contract = CONTRACTS[entity]
    df = df.reset_index(drop=True)

    try:
        empty_quarantine = df.iloc[0:0].assign(_failure_reasons="")
        return ValidationResult(entity, contract.validate(df, lazy=True), empty_quarantine)
    except pa.errors.SchemaErrors as err:
        failures = err.failure_cases

    schema_level = failures[failures["index"].isna()]
    if not schema_level.empty:
        details = schema_level[["check", "failure_case"]].drop_duplicates().to_dict("records")
        raise ContractViolationError(f"[{entity}] schema-level contract violation: {details}")

    failures = failures.assign(index=failures["index"].astype(int))

    # For duplicated keys, keep the first occurrence and quarantine only the repeats.
    is_dup_check = failures["check"] == "field_uniqueness"
    for column in failures.loc[is_dup_check, "column"].unique():
        repeats = set(df.index[df[column].duplicated(keep="first")])
        drop = is_dup_check & (failures["column"] == column) & ~failures["index"].isin(repeats)
        failures = failures[~drop]
        is_dup_check = is_dup_check[~drop]

    reasons = (
        (failures["column"].astype(str) + ":" + failures["check"].astype(str))
        .groupby(failures["index"])
        .agg(lambda s: ";".join(sorted(set(s))))
    )
    quarantine = df.loc[reasons.index].assign(_failure_reasons=reasons.to_numpy())
    valid = contract.validate(df.drop(index=reasons.index), lazy=True)
    result = ValidationResult(entity, valid, quarantine)

    if result.invalid_ratio > max_invalid_ratio:
        raise ContractViolationError(
            f"[{entity}] {result.invalid_ratio:.1%} of rows failed the contract "
            f"(error budget: {max_invalid_ratio:.0%}). Rejecting the whole batch."
        )
    return result
