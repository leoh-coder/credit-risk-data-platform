"""Every query the dashboard runs; each reads `marts.*` only (enforced by a test)."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import create_engine, text

QUERIES: dict[str, str] = {
    # ---------------------------------------------------------------- engineering
    "pipeline_freshness": """
        select
            max(finished_at)                    as last_load_at,
            max(reference_date)                 as latest_reference_date,
            count(distinct reference_date)      as dates_loaded,
            count(*)                            as loads
        from marts.dq_pipeline_loads
    """,
    "pipeline_loads": """
        select reference_date, rows_loaded, load_seconds, finished_at
        from marts.dq_pipeline_loads
        where is_latest_for_date
        order by reference_date
    """,
    "ingestion_quality": """
        select reference_date, entity, rows_received, rows_quarantined, quarantine_pct, quarantine_pct_7d,
               dates_in_window_7d
        from marts.dq_ingestion_daily
        order by reference_date, entity
    """,
    # ------------------------------------------------------------------ portfolio
    "portfolio_monthly": """
        select
            d.year_month,
            p.product_type,
            count(*)                                    as contracts,
            sum(f.principal_amount)                     as principal_amount
        from marts.fct_credit_operations as f
        inner join marts.dim_date as d on d.date_key = f.contract_date_key
        inner join marts.dim_products as p using (product_key)
        group by d.year_month, p.product_type
        order by d.year_month, p.product_type
    """,
    "cohort_default": """
        select
            d.year_month,
            p.product_type,
            sum(case when f.label_status = 'labeled' then 1 else 0 end)            as labeled,
            sum(case when f.label_status = 'labeled' then f.pd_label else 0 end)   as defaults
        from marts.fct_credit_operations as f
        inner join marts.dim_date as d on d.date_key = f.contract_date_key
        inner join marts.dim_products as p using (product_key)
        inner join marts.dim_customers as c using (customer_key)
        where not c.is_unknown_member
        group by d.year_month, p.product_type
        order by d.year_month, p.product_type
    """,
    "label_status": """
        select label_status, count(*) as contracts
        from marts.fct_credit_operations
        group by label_status
        order by label_status
    """,
    "delinquency_aging": """
        select
            p.product_type,
            i.dpd_bucket,
            count(*)                    as installments,
            sum(i.amount_overdue)       as amount_overdue
        from marts.fct_installments as i
        inner join marts.dim_products as p using (product_key)
        where i.dpd_bucket <> 'current'
        group by p.product_type, i.dpd_bucket
        order by p.product_type, i.dpd_bucket
    """,
}


def fetch(name: str, database_url) -> pd.DataFrame:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            frame = pd.read_sql(text(QUERIES[name]), connection)
    finally:
        engine.dispose()
    # numeric columns arrive as Decimal
    for column in frame.columns:
        if frame[column].dtype == object and len(frame) and type(frame[column].iat[0]).__name__ == "Decimal":
            frame[column] = frame[column].astype(float)
    return frame
