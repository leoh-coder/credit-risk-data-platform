"""Credit risk dashboard: engineering health, portfolio (star schema) and PD risk simulation.

    streamlit run dashboard/src/dashboard/app.py

Reads the Gold layer only, as the read-only `dashboard_reader` role, plus the model artifacts written by
`python -m ml.train`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy.exc import OperationalError

from dashboard import queries
from dashboard.config import get_settings
from dashboard.scoring import (
    RISK_BANDS,
    TOP_RISK_BAND,
    applicant_frame,
    load_artifacts,
    readable_input,
    risk_band,
    score,
    simulation_fields,
)
from ml.features import CATEGORICAL_FEATURES, REFERENCE_CATEGORIES

FRESHNESS_WARN_HOURS, FRESHNESS_ERROR_HOURS = 48, 168  # same as dbt source freshness (tested)
DPD_ORDER = ["1-30", "31-60", "61-89", "90+"]
RAISES_PD, LOWERS_PD = "#c0392b", "#2e86c1"


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
    except OperationalError as exc:  # warehouse unreachable: explain instead of crashing; bugs still raise
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

    # Budgets and over-budget flags come from dq_ingestion_daily (dbt vars), never re-derived here.
    warn_pct, error_pct = quality["quarantine_budget_warn_pct"].max(), quality["quarantine_budget_error_pct"].max()
    st.subheader("Quarantine rate, rolling 7 loaded dates")
    fig = px.line(quality, x="reference_date", y="quarantine_pct_7d", color="entity", labels={"quarantine_pct_7d": "%"})
    fig.add_hline(y=warn_pct, line_dash="dot", annotation_text="warn")
    fig.add_hline(y=error_pct, line_dash="dash", annotation_text="error")
    st.plotly_chart(fig, width="stretch")
    st.caption(
        f"Entity-dates over budget: {int(quality['is_over_warn_budget'].sum())} above the {warn_pct:g}% warning "
        f"budget, {int(quality['is_over_error_budget'].sum())} above the {error_pct:g}% error budget."
    )

    st.subheader("Rows loaded per reference date (latest load)")
    st.plotly_chart(px.bar(loads, x="reference_date", y="rows_loaded"), width="stretch")


# ------------------------------------------------------------------------------------------- portfolio
def portfolio_tab() -> None:
    monthly, cohorts = guarded("portfolio_monthly"), guarded("cohort_default")
    labels, aging = guarded("label_status"), guarded("delinquency_aging")
    if any(frame is None for frame in (monthly, cohorts, labels, aging)):
        return

    # Recent cohorts only label short products, so their mix would bias the rate: keep fully mature cohorts.
    immature_months = set(cohorts.loc[cohorts["immature"] > 0, "year_month"])
    mature_cohorts = cohorts[~cohorts["year_month"].isin(immature_months)]
    labeled, defaults = mature_cohorts["labeled"].sum(), mature_cohorts["defaults"].sum()
    a, b, c, d = st.columns(4)
    a.metric("Contracts", f"{int(monthly['contracts'].sum()):,}")
    b.metric("Principal (BRL)", f"{monthly['principal_amount'].sum() / 1e6:,.1f} M")
    c.metric("Labeled contracts", f"{int(cohorts['labeled'].sum()):,}")
    d.metric("Default rate (mature cohorts)", f"{defaults / labeled:.1%}" if labeled else "n/a")
    st.caption(
        f"Default rate over the {mature_cohorts['year_month'].nunique()} cohorts in which every product is mature "
        f"({int(labeled):,} labeled contracts)."
    )

    st.subheader("Contracts per month and product")
    st.plotly_chart(px.bar(monthly, x="year_month", y="contracts", color="product_type"), width="stretch")

    st.subheader("Default rate by contract cohort (90+ DPD in the first 12 installments)")
    # Same composition bias as the KPI: until a product's cohort is fully mature only its short loans carry
    # a label, so the rate is shown only for product-months without immature contracts.
    complete = cohorts[(cohorts["immature"] == 0) & (cohorts["labeled"] >= 30)]
    mature = complete.assign(default_rate=lambda f: f["defaults"] / f["labeled"])
    st.plotly_chart(
        px.line(mature, x="year_month", y="default_rate", color="product_type", markers=True),
        width="stretch",
    )
    st.caption("Only product cohorts in which every contract is mature, with at least 30 labeled contracts.")

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
    for i, field in enumerate(simulation_fields(), start=len(CATEGORICAL_FEATURES)):
        low, high, step = field.min_value, field.max_value, field.step
        start = float(np.clip(defaults["numeric"][field.feature], low, high))
        if isinstance(step, int):
            values[field.feature] = columns[i % 3].number_input(
                field.label, int(low), int(high), round(start), step, key=field.feature
            )
        else:
            values[field.feature] = columns[i % 3].number_input(field.label, low, high, start, step, key=field.feature)
    return values


def contribution_chart(parts: pd.Series) -> go.Figure:
    terms = parts.drop("intercept").sort_values(key=np.abs)
    colors = np.where(terms > 0, RAISES_PD, LOWERS_PD)
    fig = go.Figure(go.Bar(x=terms.values, y=terms.index, orientation="h", marker_color=colors))
    fig.update_layout(xaxis_title="Contribution to log-odds (red raises PD, blue lowers it)", height=480)
    return fig


def odds_ratio_chart(coefficients: pd.DataFrame) -> go.Figure:
    """Each odds ratio as a bar from 1 on a log axis, so its direction and size read at a glance."""
    odds = coefficients.sort_values("odds_ratio")
    labels = [readable_input(name, feature) for name, feature in zip(odds["input"], odds["feature"], strict=True)]
    ratios = odds["odds_ratio"].to_numpy()
    fig = go.Figure(
        go.Bar(
            x=ratios - 1,
            base=1,
            y=labels,
            orientation="h",
            marker_color=np.where(ratios > 1, RAISES_PD, LOWERS_PD),
            customdata=ratios,
            hovertemplate="%{y}: %{customdata:.2f}<extra></extra>",
        )
    )
    low = math.floor(math.log2(min(ratios.min(), 1.0)))
    high = math.ceil(math.log2(max(ratios.max(), 1.0)))
    ticks = [2.0**power for power in range(low, high + 1)]
    fig.add_vline(x=1, line_color="#7f8c8d")
    fig.update_xaxes(type="log", tickvals=ticks, ticktext=[f"{tick:g}" for tick in ticks])
    fig.update_layout(xaxis_title="Odds ratio, log scale (red raises PD, blue lowers it)", height=520)
    return fig


def risk_tab() -> None:
    artifacts = model_artifacts()
    if artifacts is None:
        st.info("No trained model found. Train it with `make train` (writes the artifacts this tab reads).")
        return
    if not artifacts.is_current:
        st.warning("Model artifacts are out of date: run `make train`")
        return

    st.subheader("Simulate an applicant")
    values = simulation_form(artifacts.simulation_defaults)
    pd_hat, parts = score(artifacts.model, values)

    a, b, c = st.columns(3)
    a.metric("Probability of default", f"{pd_hat:.1%}")
    b.metric("Risk band", risk_band(pd_hat))
    ratio = applicant_frame(values).at[0, "reference_installment_to_income_ratio"]
    c.metric("Installment / income at the reference rate", f"{ratio:.1%}")
    st.caption(
        "Bands: " + ", ".join(f"{band} < {upper:.0%}" for band, upper in RISK_BANDS) + f", {TOP_RISK_BAND} otherwise "
        "(display only, not a credit policy)."
    )
    st.plotly_chart(contribution_chart(parts), width="stretch")
    st.caption(
        f"Exact decomposition of the logistic model: intercept {parts['intercept']:+.2f} + feature terms = "
        f"log-odds {parts.sum():+.2f}, and PD = 1 / (1 + e^-log-odds) = {pd_hat:.1%}. "
        "A term is coefficient x the standardized (log for money) value, relative to the training mean."
    )

    st.subheader("Model performance (out of time)")
    periods, metric_names = ("train", "validation", "test"), ("auc", "gini", "ks", "brier")
    rows = []
    for name, model_metrics in artifacts.metrics["models"].items():
        for period in periods:
            m = model_metrics[period]
            rows.append({"model": name, "period": period, **{k: m[k] for k in metric_names}})
    st.dataframe(
        pd.DataFrame(rows),
        column_config={k: st.column_config.NumberColumn(format="%.3f") for k in metric_names},
        hide_index=True,
        width="stretch",
    )
    split = artifacts.metrics["split"]
    st.caption(
        ", ".join(
            f"{p} {split[p]['from']}..{split[p]['to']} ({split[p]['rows']:,} contracts)" for p in periods
        ).capitalize()
        + ". Hyperparameters are chosen on validation; the final model is refit on train + validation "
        "and scored once on test. Never a random split."
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

    references = ", ".join(f"{f} = {c}" for f, c in REFERENCE_CATEGORIES.items())
    st.subheader("Odds ratios (per +1 SD; a category vs. the reference category)")
    st.caption(f"Reference categories: {references}. Money inputs enter the model as log(1 + amount).")
    st.plotly_chart(odds_ratio_chart(artifacts.coefficients), width="stretch")


st.title("Credit Risk Data Platform")
st.caption("Gold layer only, read as `dashboard_reader`. Synthetic data.")
engineering, portfolio, risk = st.tabs(["Engineering", "Portfolio", "Risk simulation"])
with engineering:
    engineering_tab()
with portfolio:
    portfolio_tab()
with risk:
    risk_tab()
