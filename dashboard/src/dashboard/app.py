"""Credit risk dashboard: engineering health, portfolio (star schema) and PD risk simulation.

    streamlit run dashboard/src/dashboard/app.py

Reads the Gold layer only, as the read-only `dashboard_reader` role, plus the model artifacts written by
`python -m ml.train`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import queries
from dashboard.config import get_settings
from dashboard.scoring import RISK_BANDS, load_artifacts, risk_band, score
from ml.features import CATEGORICAL_FEATURES

DQ_QUARANTINE_WARN_PCT, DQ_QUARANTINE_ERROR_PCT = 5, 10  # same budget as the dbt vars
FRESHNESS_WARN_HOURS, FRESHNESS_ERROR_HOURS = 48, 168  # same as dbt source freshness
DPD_ORDER = ["1-30", "31-60", "61-89", "90+"]

# label, min, max, step for each simulated input (installment_to_income_ratio is derived, not typed)
FIELDS: dict[str, tuple[str, float, float, float]] = {
    "principal_amount": ("Principal (BRL)", 500.0, 500_000.0, 500.0),
    "installments_count": ("Installments", 1, 120, 1),
    "installment_amount": ("Installment (BRL)", 10.0, 50_000.0, 10.0),
    "bureau_score_at_origination": ("Bureau score", 0, 1000, 10),
    "age_at_registration": ("Age", 18, 100, 1),
    "monthly_income": ("Monthly income (BRL)", 0.0, 200_000.0, 100.0),
    "of_tx_count": ("Open Finance transactions (90 d)", 0, 2000, 1),
    "of_salary_deposit_count": ("Salary deposits (90 d)", 0, 20, 1),
    "of_monthly_inflow": ("Monthly inflow (BRL)", 0.0, 500_000.0, 100.0),
    "of_monthly_outflow": ("Monthly outflow (BRL)", 0.0, 500_000.0, 100.0),
    "of_inflow_to_income_ratio": ("Inflow / declared income", 0.0, 20.0, 0.05),
    "of_betting_spend_share": ("Betting share of spend", 0.0, 1.0, 0.01),
    "of_cash_withdrawal_share": ("Cash withdrawal share of spend", 0.0, 1.0, 0.01),
}

st.set_page_config(page_title="Credit Risk Data Platform", layout="wide")


@st.cache_data(ttl=300, show_spinner=False)
def load(name: str) -> pd.DataFrame:
    return queries.fetch(name, get_settings().database_url)


@st.cache_resource(show_spinner=False)
def model_artifacts():
    return load_artifacts(get_settings().artifacts_dir)


def guarded(name: str) -> pd.DataFrame | None:
    try:
        return load(name)
    except Exception as exc:  # the page must explain, not crash, when the warehouse is down
        st.error(f"Could not read `{name}` from the warehouse: {type(exc).__name__}. Is `make up` running?")
        return None


# ----------------------------------------------------------------------------------------- engineering
def engineering_tab() -> None:
    fresh, loads, quality = guarded("pipeline_freshness"), guarded("pipeline_loads"), guarded("ingestion_quality")
    if fresh is None or loads is None or quality is None:
        return
    if fresh.empty or pd.isna(fresh.at[0, "last_load_at"]):
        st.info("Nothing loaded yet: run the ingestion, `make load` and `make transform`.")
        return

    last = pd.Timestamp(fresh.at[0, "last_load_at"])
    age_hours = (pd.Timestamp(datetime.now(UTC).replace(tzinfo=None)) - last).total_seconds() / 3600
    status = "OK" if age_hours < FRESHNESS_WARN_HOURS else "WARN" if age_hours < FRESHNESS_ERROR_HOURS else "ERROR"
    a, b, c, d = st.columns(4)
    a.metric("Last load (UTC)", last.strftime("%Y-%m-%d %H:%M"), f"{age_hours:.0f} h ago: {status}", delta_color="off")
    b.metric("Latest reference date", str(fresh.at[0, "latest_reference_date"]))
    c.metric("Dates loaded", f"{int(fresh.at[0, 'dates_loaded']):,}")
    d.metric("Loads (incl. reloads)", f"{int(fresh.at[0, 'loads']):,}")

    st.subheader("Quarantine rate, rolling 7 loaded dates")
    fig = px.line(quality, x="reference_date", y="quarantine_pct_7d", color="entity", labels={"quarantine_pct_7d": "%"})
    fig.add_hline(y=DQ_QUARANTINE_WARN_PCT, line_dash="dot", annotation_text="warn")
    fig.add_hline(y=DQ_QUARANTINE_ERROR_PCT, line_dash="dash", annotation_text="error")
    st.plotly_chart(fig, width="stretch")
    full = quality[quality["dates_in_window_7d"] == 7]  # same rule as the dbt budget test
    over = full[full["quarantine_pct_7d"] > DQ_QUARANTINE_WARN_PCT]
    st.caption(
        f"{len(over)} entity-dates above the {DQ_QUARANTINE_WARN_PCT}% warning budget "
        "(evaluated once the window holds 7 loaded dates)."
    )

    st.subheader("Rows loaded per reference date (latest load)")
    st.plotly_chart(px.bar(loads, x="reference_date", y="rows_loaded"), width="stretch")


# ------------------------------------------------------------------------------------------- portfolio
def portfolio_tab() -> None:
    monthly, cohorts = guarded("portfolio_monthly"), guarded("cohort_default")
    labels, aging = guarded("label_status"), guarded("delinquency_aging")
    if any(frame is None for frame in (monthly, cohorts, labels, aging)):
        return

    labeled, defaults = cohorts["labeled"].sum(), cohorts["defaults"].sum()
    a, b, c, d = st.columns(4)
    a.metric("Contracts", f"{int(monthly['contracts'].sum()):,}")
    b.metric("Principal (BRL)", f"{monthly['principal_amount'].sum() / 1e6:,.1f} M")
    c.metric("Labeled contracts", f"{int(labeled):,}")
    d.metric("Default rate (labeled)", f"{defaults / labeled:.1%}" if labeled else "n/a")

    st.subheader("Contracts per month and product")
    st.plotly_chart(px.bar(monthly, x="year_month", y="contracts", color="product_type"), width="stretch")

    st.subheader("Default rate by contract cohort (90+ DPD in the first 12 installments)")
    mature = cohorts[cohorts["labeled"] >= 30].assign(default_rate=lambda f: f["defaults"] / f["labeled"])
    st.plotly_chart(
        px.line(mature, x="year_month", y="default_rate", color="product_type", markers=True),
        width="stretch",
    )
    st.caption("Only cohorts with at least 30 labeled contracts; recent cohorts are not mature yet.")

    left, right = st.columns(2)
    left.subheader("Label status")
    left.plotly_chart(px.bar(labels, x="label_status", y="contracts"), width="stretch")
    right.subheader("Overdue amount by DPD bucket")
    right.plotly_chart(
        px.bar(
            aging, x="dpd_bucket", y="amount_overdue", color="product_type", category_orders={"dpd_bucket": DPD_ORDER}
        ),
        width="stretch",
    )


# ------------------------------------------------------------------------------------- risk simulation
def simulation_form(defaults: dict) -> dict:
    values: dict = {}
    columns = st.columns(3)
    for i, feature in enumerate(CATEGORICAL_FEATURES):
        spec = defaults["categorical"][feature]
        options = spec["options"]
        values[feature] = columns[i % 3].selectbox(
            feature.replace("_", " ").capitalize(), options, index=options.index(spec["default"]), key=feature
        )
    for i, (feature, (label, low, high, step)) in enumerate(FIELDS.items(), start=len(CATEGORICAL_FEATURES)):
        start = float(np.clip(defaults["numeric"][feature], low, high))
        if isinstance(step, int):
            values[feature] = columns[i % 3].number_input(
                label, int(low), int(high), int(round(start)), int(step), key=feature
            )
        else:
            values[feature] = columns[i % 3].number_input(label, low, high, start, step, key=feature)
    return values


def contribution_chart(parts: pd.Series) -> go.Figure:
    terms = parts.drop("intercept").sort_values(key=np.abs)
    colors = np.where(terms > 0, "#c0392b", "#2e86c1")
    fig = go.Figure(go.Bar(x=terms.values, y=terms.index, orientation="h", marker_color=colors))
    fig.update_layout(xaxis_title="Contribution to log-odds (red raises PD, blue lowers it)", height=480)
    return fig


def risk_tab() -> None:
    artifacts = model_artifacts()
    if artifacts is None:
        st.info("No trained model found. Train it with `make train` (writes the artifacts this tab reads).")
        return

    st.subheader("Simulate an applicant")
    values = simulation_form(artifacts.simulation_defaults)
    pd_hat, parts = score(artifacts.model, values)

    a, b, c = st.columns(3)
    a.metric("Probability of default", f"{pd_hat:.1%}")
    b.metric("Risk band", risk_band(pd_hat))
    ratio = values["installment_amount"] / values["monthly_income"] if values["monthly_income"] else float("nan")
    c.metric("Installment / income (derived)", f"{ratio:.1%}")
    st.caption(
        "Bands: " + ", ".join(f"{band} < {upper:.0%}" for band, upper in RISK_BANDS[:-1]) + ", E otherwise "
        "(display only, not a credit policy)."
    )
    st.plotly_chart(contribution_chart(parts), width="stretch")
    st.caption(
        f"Exact decomposition of the logistic model: intercept {parts['intercept']:+.2f} + feature terms = "
        f"log-odds {parts.sum():+.2f}, and PD = 1 / (1 + e^-log-odds) = {pd_hat:.1%}. "
        "A term is coefficient x the standardized (log for money) value, relative to the training mean."
    )

    st.subheader("Model performance (out-of-time test)")
    rows = []
    for name, model_metrics in artifacts.metrics["models"].items():
        for period in ("train", "test"):
            m = model_metrics[period]
            rows.append({"model": name, "period": period, **{k: m[k] for k in ("auc", "gini", "ks", "brier")}})
    st.dataframe(pd.DataFrame(rows).round(3), hide_index=True, width="stretch")
    split = artifacts.metrics["split"]
    st.caption(
        f"Train {split['train']['from']}..{split['train']['to']} ({split['train']['rows']:,} contracts), "
        f"test {split['test']['from']}..{split['test']['to']} ({split['test']['rows']:,}). Never a random split."
    )

    left, right = st.columns(2)
    calibration = artifacts.calibration
    fig = px.scatter(calibration, x="mean_pd", y="observed_rate", size="n", labels={"mean_pd": "predicted PD"})
    top = float(max(calibration["mean_pd"].max(), calibration["observed_rate"].max()))
    fig.add_shape(type="line", x0=0, y0=0, x1=top, y1=top, line_dash="dot")
    left.subheader("Calibration by predicted-PD decile")
    left.plotly_chart(fig, width="stretch")

    importance = artifacts.permutation_importance.sort_values("auc_drop_mean")
    right.subheader("Permutation importance (AUC drop)")
    right.plotly_chart(
        px.bar(importance, x="auc_drop_mean", y="feature", orientation="h", error_x="auc_drop_std"),
        width="stretch",
    )

    st.subheader("Odds ratios (per +1 SD; a category vs. the rest)")
    odds = artifacts.coefficients.reindex(artifacts.coefficients["coefficient"].abs().sort_values().index)
    st.plotly_chart(px.bar(odds, x="odds_ratio", y="input", orientation="h", log_x=True), width="stretch")


st.title("Credit Risk Data Platform")
st.caption("Gold layer only, read as `dashboard_reader`. Synthetic data.")
engineering, portfolio, risk = st.tabs(["Engineering", "Portfolio", "Risk simulation"])
with engineering:
    engineering_tab()
with portfolio:
    portfolio_tab()
with risk:
    risk_tab()
