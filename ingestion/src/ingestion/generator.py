"""Synthetic core-banking source system.

Simulates what a Brazilian retail bank's operational systems would export for
one *reference date* (one daily batch):

- customers          -> onboarding / KYC system (cadastral data)
- credit_operations  -> loan origination system (contracts granted that day)
- installments       -> loan servicing system (payment schedule + observed payments)
- transactions       -> account history shared via Open Finance at onboarding
                        (behavioral data from the `history_days` before the application)

Design principles
-----------------
1. Deterministic: the same (seed, reference_date, as_of) always yields the same
   batch. That makes reruns idempotent and bugs reproducible.
2. Vectorized with NumPy: no Python loops over rows except for Faker strings.
3. Hidden latent risk: every customer gets an unobserved risk factor `z` that
   drives BOTH the observable features (income, bureau score, betting spend...)
   AND the probability of default. `z` is never exported, so a downstream model
   has to learn risk from observable signals, exactly like in real life.
4. Right-censoring: payments are only "observed" up to `as_of`. Recent contracts
   have immature performance; the ML layer must respect a performance window.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time

import numpy as np
import pandas as pd
from faker import Faker

GENERATOR_VERSION = "1.1.0"  # 1.1.0: unique synthetic CPFs (Faker's collided)

# Faker's pt_BR cpf() draws 9 *distinct* digits: only 10!/1! ~ 3.6 M values, so ~1 in 40 customers
# shared a CPF at 25 k customers. CPFs now come from a per-(date, row) sequence scrambled by a
# bijection mod 10^9 (odd multiplier, not divisible by 5), unique within any 10,000-day window.
_CPF_MULTIPLIER = 7_919_239
MAX_CUSTOMERS_PER_BATCH = 2_500  # with rows 1..2_500 no sequence maps to an invalid repdigit CPF

# Approximate population share per Brazilian state (IBGE-inspired, normalized below).
STATE_WEIGHTS: dict[str, float] = {
    "SP": 21.9,
    "MG": 10.0,
    "RJ": 7.9,
    "BA": 6.9,
    "PR": 5.6,
    "RS": 5.3,
    "PE": 4.5,
    "CE": 4.4,
    "PA": 4.0,
    "SC": 3.7,
    "GO": 3.5,
    "MA": 3.3,
    "AM": 1.9,
    "ES": 1.9,
    "PB": 1.9,
    "RN": 1.6,
    "MT": 1.8,
    "AL": 1.5,
    "PI": 1.6,
    "DF": 1.4,
    "MS": 1.4,
    "SE": 1.1,
    "RO": 0.8,
    "TO": 0.7,
    "AC": 0.4,
    "AP": 0.4,
    "RR": 0.3,
}
STATES = np.array(list(STATE_WEIGHTS))
STATE_P = np.array(list(STATE_WEIGHTS.values())) / sum(STATE_WEIGHTS.values())

EMPLOYMENT_TYPES = np.array(["clt", "public_servant", "self_employed", "retired", "informal"])
_EMPLOYMENT_BASE_P = np.array([0.45, 0.15, 0.18, 0.12, 0.10])
_EMPLOYMENT_RISK_SHIFT = np.array([-0.2, -0.5, 0.2, -0.1, 0.6])  # how latent risk tilts the mix
_BASE_INCOME = {
    "clt": 3_500,
    "public_servant": 6_500,
    "self_employed": 4_500,
    "retired": 3_000,
    "informal": 2_000,
}
PAYROLL_ELIGIBLE = {"clt", "public_servant", "retired"}

PRODUCT_TYPES = np.array(["personal_loan", "payroll_loan", "vehicle_financing"])
_PRODUCT_RATE = {  # monthly interest rate range (min, max)
    "personal_loan": (0.030, 0.085),
    "payroll_loan": (0.015, 0.024),
    "vehicle_financing": (0.012, 0.030),
}
_PRODUCT_TERMS = {
    "personal_loan": np.array([6, 12, 18, 24]),
    "payroll_loan": np.array([24, 36, 48, 60, 72, 84]),
    "vehicle_financing": np.array([24, 36, 48, 60]),
}
_PRODUCT_INCOME_MULT = {  # principal as a multiple of monthly income
    "personal_loan": (0.5, 3.0),
    "payroll_loan": (2.0, 10.0),
    "vehicle_financing": (4.0, 12.0),
}
_PRODUCT_DEFAULT_EFFECT = {"personal_loan": 0.4, "payroll_loan": -1.2, "vehicle_financing": -0.9}
PAYROLL_MAX_COMMITMENT = 0.35  # max share of income committed to payroll-deductible installments

TRANSACTION_TYPES_CREDIT = np.array(["salary_deposit", "pix_in"])
TRANSACTION_TYPES_DEBIT = np.array(["pix_out", "debit_card", "boleto_payment", "cash_withdrawal"])
TRANSACTION_TYPES = np.concatenate([TRANSACTION_TYPES_CREDIT, TRANSACTION_TYPES_DEBIT])
MERCHANT_CATEGORIES = np.array(
    ["groceries", "utilities", "restaurants", "transport", "health", "retail", "betting"]
)
_MERCHANT_BASE_P = np.array([0.28, 0.15, 0.17, 0.14, 0.08, 0.18])  # excludes betting
CHANNELS = np.array(["mobile_app", "internet_banking", "branch", "atm"])


@dataclass(frozen=True)
class BatchContext:
    reference_date: date
    as_of: date
    seed: int

    @property
    def batch_id(self) -> str:
        raw = f"{self.seed}|{self.reference_date}|{self.as_of}|{GENERATOR_VERSION}"
        return f"{self.reference_date:%Y%m%d}-{hashlib.sha1(raw.encode()).hexdigest()[:8]}"

    @property
    def extracted_at(self) -> pd.Timestamp:
        # Convention: all timestamps in the lake are naive UTC.
        return pd.Timestamp(datetime.combine(self.as_of, time(23, 59, 59)))


def _with_cpf_check_digits(base: str) -> str:
    digits = [int(c) for c in base]
    for size in (9, 10):
        remainder = sum(d * w for d, w in zip(digits, range(size + 1, 1, -1), strict=True)) % 11
        digits.append(0 if remainder < 2 else 11 - remainder)
    return "".join(map(str, digits))


def synthetic_cpfs(reference_date: date, n: int) -> list[str]:
    if n > MAX_CUSTOMERS_PER_BATCH:
        raise ValueError(f"at most {MAX_CUSTOMERS_PER_BATCH} customers per batch (CPF scheme), got {n}")
    cpfs = []
    for row in range(1, n + 1):
        sequence = (reference_date.toordinal() % 10_000) * 100_000 + row
        full = _with_cpf_check_digits(f"{sequence * _CPF_MULTIPLIER % 1_000_000_000:09d}")
        cpfs.append(f"{full[:3]}.{full[3:6]}.{full[6:9]}-{full[9:]}")
    return cpfs


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _sample_rows(rng: np.random.Generator, probs: np.ndarray) -> np.ndarray:
    """Vectorized categorical sampling where each row has its own probability vector."""
    probs = probs / probs.sum(axis=1, keepdims=True)
    u = rng.random((probs.shape[0], 1))
    return (u > probs.cumsum(axis=1)).sum(axis=1)


def _add_months(start: pd.Timestamp, months: np.ndarray) -> pd.DatetimeIndex:
    total = start.month - 1 + months
    return pd.to_datetime(
        {"year": start.year + total // 12, "month": total % 12 + 1, "day": min(start.day, 28)}
    )


def _metadata(df: pd.DataFrame, ctx: BatchContext) -> pd.DataFrame:
    """Lineage columns: every raw row knows which batch and contract produced it."""
    return df.assign(
        _batch_id=ctx.batch_id,
        _reference_date=pd.Timestamp(ctx.reference_date),
        _extracted_at=ctx.extracted_at,
        _generator_version=GENERATOR_VERSION,
    )


def generate_batch(
    reference_date: date,
    as_of: date,
    seed: int = 42,
    customers_per_batch: int = 150,
    history_days: int = 90,
) -> dict[str, pd.DataFrame]:
    if as_of < reference_date:
        raise ValueError(f"as_of ({as_of}) cannot be earlier than reference_date ({reference_date})")

    ctx = BatchContext(reference_date, as_of, seed)
    rng = np.random.default_rng([seed, reference_date.toordinal()])
    fake = Faker("pt_BR")
    fake.seed_instance(seed * 1_000_003 + reference_date.toordinal())
    ref_ts = pd.Timestamp(reference_date)

    # ------------------------------------------------------------------ customers
    n = max(1, int(rng.poisson(customers_per_batch)))
    z = rng.standard_normal(n)  # hidden latent risk, never exported

    age = np.clip(rng.normal(40, 12, n), 18, 80).astype(int)
    emp_logits = np.log(_EMPLOYMENT_BASE_P)[None, :] + z[:, None] * _EMPLOYMENT_RISK_SHIFT[None, :]
    employment = EMPLOYMENT_TYPES[_sample_rows(rng, np.exp(emp_logits))]
    employment = np.where((employment == "retired") & (age < 50), "clt", employment)
    employment = np.where((age >= 65) & (rng.random(n) < 0.8), "retired", employment)

    base_income = np.array([_BASE_INCOME[e] for e in employment])
    income = np.round(base_income * rng.lognormal(0, 0.45, n) * np.exp(-0.15 * z), 2)
    bureau_score = np.clip(np.round(1000 * _sigmoid(1.0 - 1.1 * z + rng.normal(0, 0.35, n))), 0, 1000).astype(
        int
    )

    birth_date = ref_ts - pd.to_timedelta(age * 365 + rng.integers(0, 365, n), unit="D")
    names = [fake.name() for _ in range(n)]
    customers = pd.DataFrame(
        {
            "customer_id": [f"CUS{reference_date:%Y%m%d}{i:05d}" for i in range(n)],
            "cpf": synthetic_cpfs(reference_date, n),
            "full_name": names,
            "email": [fake.free_email() for _ in range(n)],
            "phone_number": [fake.cellphone_number() for _ in range(n)],
            "birth_date": birth_date.normalize(),
            "state": rng.choice(STATES, size=n, p=STATE_P),
            "employment_type": employment,
            "monthly_income": income,
            "registration_date": ref_ts,
        }
    )

    # ---------------------------------------------------------- credit_operations
    ops_per_customer = 1 + (rng.random(n) < 0.25).astype(int)
    c_idx = np.repeat(np.arange(n), ops_per_customer)
    n_ops = len(c_idx)

    eligible = np.isin(employment[c_idx], list(PAYROLL_ELIGIBLE))
    prod_p = np.column_stack([np.full(n_ops, 0.5), np.where(eligible, 0.3, 0.0), np.full(n_ops, 0.2)])
    product = PRODUCT_TYPES[_sample_rows(rng, prod_p)]

    op_income = income[c_idx]
    op_score = bureau_score[c_idx]
    lo = np.array([_PRODUCT_RATE[p][0] for p in product])
    hi = np.array([_PRODUCT_RATE[p][1] for p in product])
    rate = np.round(lo + (hi - lo) * (1 - op_score / 1000) + rng.normal(0, 0.002, n_ops), 4)
    rate = np.clip(rate, lo, hi)  # risk-based pricing: worse score -> higher rate

    terms = np.array([rng.choice(_PRODUCT_TERMS[p]) for p in product])
    mult_lo = np.array([_PRODUCT_INCOME_MULT[p][0] for p in product])
    mult_hi = np.array([_PRODUCT_INCOME_MULT[p][1] for p in product])
    principal = op_income * rng.uniform(mult_lo, mult_hi)

    annuity = rate / (1 - (1 + rate) ** -terms)  # Price table (French amortization)
    is_payroll = product == "payroll_loan"
    max_principal = PAYROLL_MAX_COMMITMENT * op_income / annuity
    principal = np.where(is_payroll, np.minimum(principal, max_principal), principal)
    principal = np.maximum(np.round(principal, -2), 500.0)
    installment_amount = np.round(principal * annuity, 2)
    dti = installment_amount / op_income

    credit_operations = pd.DataFrame(
        {
            "operation_id": [f"OPR{reference_date:%Y%m%d}{i:05d}" for i in range(n_ops)],
            "customer_id": customers["customer_id"].to_numpy()[c_idx],
            "product_type": product,
            "contract_date": ref_ts,
            "principal_amount": principal,
            "interest_rate_monthly": rate,
            "installments_count": terms,
            "installment_amount": installment_amount,
            "bureau_score_at_origination": op_score,
        }
    )

    # ------------------------------------------------- default ground truth (hidden)
    product_effect = np.array([_PRODUCT_DEFAULT_EFFECT[p] for p in product])
    youth_effect = 0.03 * np.clip(30 - age[c_idx], 0, None)
    default_logit = -2.9 + 1.2 * z[c_idx] + 4.0 * (dti - 0.15) + product_effect + youth_effect
    will_default = rng.random(n_ops) < _sigmoid(default_logit)
    # defaults concentrate early in the contract life (first-payment / early default)
    default_start = np.minimum(1 + rng.geometric(0.22, n_ops), terms)

    # --------------------------------------------------------------- installments
    op_idx = np.repeat(np.arange(n_ops), terms)
    starts = np.repeat(np.cumsum(terms) - terms, terms)
    k = np.arange(len(op_idx)) - starts + 1  # installment number, 1..term

    due_date = _add_months(ref_ts, k)
    defaulted_here = will_default[op_idx] & (k >= default_start[op_idx])

    p_late = 0.06 + 0.25 * _sigmoid(1.5 * z[c_idx][op_idx] - 1)
    is_late = rng.random(len(k)) < p_late
    delay_days = np.where(is_late, rng.geometric(0.12, len(k)), -rng.integers(0, 5, len(k)))
    payment_date = due_date + pd.to_timedelta(delay_days, unit="D")

    observed = (~defaulted_here) & (payment_date <= pd.Timestamp(as_of))
    amount_due = installment_amount[op_idx]

    installments = pd.DataFrame(
        {
            "installment_id": [
                f"{credit_operations['operation_id'].iat[o]}-{n_:03d}"
                for o, n_ in zip(op_idx, k, strict=True)
            ],
            "operation_id": credit_operations["operation_id"].to_numpy()[op_idx],
            "installment_number": k,
            "due_date": due_date,
            "amount_due": amount_due,
            "payment_date": pd.Series(payment_date).where(observed, pd.NaT).to_numpy(),
            "amount_paid": np.where(observed, amount_due, 0.0),
        }
    )

    # --------------------------------------------------------------- transactions
    tx_per_customer = rng.poisson(history_days * 0.55, n)
    t_idx = np.repeat(np.arange(n), tx_per_customer)
    n_tx = len(t_idx)
    tz = z[t_idx]
    t_income = income[t_idx]

    is_credit = rng.random(n_tx) < 0.25
    credit_type = TRANSACTION_TYPES_CREDIT[(rng.random(n_tx) < 0.5).astype(int)]
    debit_p = np.column_stack(
        [
            np.full(n_tx, 0.30),
            np.full(n_tx, 0.40),
            np.full(n_tx, 0.20),
            0.05 + 0.10 * _sigmoid(2 * tz),  # cash withdrawals grow with latent risk
        ]
    )
    debit_type = TRANSACTION_TYPES_DEBIT[_sample_rows(rng, debit_p)]
    tx_type = np.where(is_credit, credit_type, debit_type)

    p_bet = 0.01 + 0.18 * _sigmoid(2.2 * tz - 1.5)  # online betting share grows with risk
    merchant_p = np.column_stack([_MERCHANT_BASE_P[None, :] * (1 - p_bet)[:, None], p_bet[:, None]])
    merchant = MERCHANT_CATEGORIES[_sample_rows(rng, merchant_p)].astype(object)
    has_merchant = np.isin(tx_type, ["debit_card", "pix_out", "boleto_payment"])
    merchant = np.where(has_merchant, merchant, None)

    amount = np.where(
        is_credit,
        t_income * 0.35 * rng.lognormal(0, 0.5, n_tx),
        t_income * 0.09 * rng.lognormal(0, 0.8, n_tx) * (1 + 0.15 * np.clip(tz, -1, 3)),
    )
    channel = np.where(
        tx_type == "cash_withdrawal",
        "atm",
        CHANNELS[:3][_sample_rows(rng, np.tile([0.8, 0.15, 0.05], (n_tx, 1)))],
    )
    seconds_back = rng.integers(60, history_days * 86_400, n_tx)
    tx_ts = ref_ts - pd.to_timedelta(seconds_back, unit="s")

    transactions = (
        pd.DataFrame(
            {
                "transaction_id": [f"TRX{reference_date:%Y%m%d}{i:07d}" for i in range(n_tx)],
                "customer_id": customers["customer_id"].to_numpy()[t_idx],
                "transaction_ts": tx_ts,
                "transaction_type": tx_type,
                "direction": np.where(is_credit, "credit", "debit"),
                "amount": np.round(np.maximum(amount, 1.0), 2),
                "merchant_category": merchant,
                "channel": channel,
            }
        )
        .sort_values("transaction_ts", kind="stable")
        .reset_index(drop=True)
    )

    return {
        name: _metadata(df, ctx)
        for name, df in {
            "customers": customers,
            "credit_operations": credit_operations,
            "installments": installments,
            "transactions": transactions,
        }.items()
    }


# ---------------------------------------------------------------------------
# Fault injection: real source systems send dirty data. We simulate it on purpose
# so the data-contract gate has something to catch.
# ---------------------------------------------------------------------------
def inject_anomalies(
    batch: dict[str, pd.DataFrame], rate: float, seed: int, reference_date: date
) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    if rate <= 0:
        return batch, {name: 0 for name in batch}

    rng = np.random.default_rng([seed, reference_date.toordinal(), 7])

    def future_ts(col):
        def f(df, i):
            df.loc[i, col] = df.loc[i, "_extracted_at"] + pd.Timedelta(days=30)

        return f

    def neg(col):
        def f(df, i):
            df.loc[i, col] = -abs(df.loc[i, col])

        return f

    def setv(col, value):
        def f(df, i):
            df.loc[i, col] = value

        return f

    anomalies = {
        "customers": [
            neg("monthly_income"),
            setv("state", "XX"),
            setv("cpf", None),
            setv("birth_date", pd.Timestamp(reference_date) - pd.Timedelta(days=15 * 365)),
        ],
        "credit_operations": [
            neg("principal_amount"),
            setv("interest_rate_monthly", 1.5),
            setv("customer_id", None),
        ],
        "installments": [neg("amount_due"), setv("installment_number", 0)],
        "transactions": [
            neg("amount"),
            future_ts("transaction_ts"),
            setv("transaction_type", "unknown_type"),
        ],
    }

    out, counts = {}, {}
    for name, df in batch.items():
        df = df.copy()
        k = int(rng.binomial(len(df), rate))
        rows = rng.choice(df.index.to_numpy(), size=k, replace=False) if k else []
        for row in rows:
            anomalies[name][rng.integers(len(anomalies[name]))](df, row)
        n_dupes = int(rng.binomial(len(df), rate / 4))  # duplicated primary keys (at-least-once delivery)
        if n_dupes:
            df = pd.concat([df, df.sample(n=n_dupes, random_state=int(rng.integers(1e9)))], ignore_index=True)
        out[name], counts[name] = df, k + n_dupes
    return out, counts
