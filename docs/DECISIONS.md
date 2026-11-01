# Technical decisions

Each entry: the decision, why, and the alternative that was rejected.

## Ingestion and lake

### D01. SeaweedFS as the S3-compatible object store
- **Why:** speaks the S3 API, so the lake code is identical to AWS S3 (only `S3_ENDPOINT_URL` changes); actively maintained.
- **Rejected:** MinIO. Its community Docker images were discontinued and the repository archived.

### D02. Synthetic source with a hidden latent risk factor
- **Why:** one unexported factor drives both the observable signals (income, bureau score, employment, betting and cash-withdrawal behavior) and default, so the model has to learn from observable data, as with a real portfolio. Default rate in mature cohorts is about 11%, with payroll < vehicle < personal loans.
- **Rejected:** independent random columns (nothing to learn) or exporting the risk driver (trivial, leaky model).

### D03. Daily batches, deterministic per (seed, reference date, as-of)
- **Why:** reruns, CI and the whole lake are reproducible byte for byte. `--as-of` freezes the observation clock, so payments after it are not observed (right-censoring is explicit).
- **Rejected:** wall-clock generation, which makes bugs and test results non-reproducible.

### D04. Open Finance history strictly before the application date
- **Why:** behavioral features must describe what the bank could see when deciding.
- **Rejected:** including post-decision transactions, which leaks the outcome into the features.

### D05. Data contracts at the lake boundary (Pandera)
- **Why:** bad data is stopped and explained before it reaches the warehouse.
  - Schema violations (missing or extra column) reject the whole batch; `strict=True`, so schema drift requires a new contract version.
  - Row violations go to `quarantine/` with `_failure_reasons`; duplicated keys keep the first occurrence.
  - Circuit breaker: the batch is rejected only if invalid rows exceed 10% of the entity **and** 5 rows. The absolute floor keeps a few bad rows in a small batch (30 customers) from rejecting it.
- **Rejected:** validating only in dbt (bad rows already landed), and a ratio-only breaker (permanently rejected 2% of dates on small batches).

### D06. Lake layout with a manifest as the commit marker
- **Why:** `raw|quarantine/<entity>/reference_date=YYYY-MM-DD/part-0.parquet`; each run replaces its own partitions (idempotent). The manifest is deleted first (only after validation passes, so a rejected rerun keeps the previous commit) and written last, so a reader never pairs an old manifest with half-written files.
- **Rejected:** appending files per run (duplicates on rerun) or a separate catalog service (more infrastructure for the same guarantee).

### D07. Lineage columns on every raw row
- **Why:** `_batch_id`, `_reference_date`, `_extracted_at`, `_generator_version` (plus `_load_id`, `_loaded_at` in the warehouse) trace any row back to its batch and load. Timestamps are naive UTC.
- **Rejected:** lineage only in logs, which cannot be joined to the data.

### D08. Unique synthetic CPFs from a scrambled per-date sequence
- **Why:** the faker CPF provider draws 9 distinct digits (~3.6 M values), and 86 of ~25 k customers collided, which the warehouse uniqueness test caught. A bijection mod 10^9 over (date, row) with valid check digits is unique over any 10,000-day window. Batches are capped at 2,500 customers so no sequence maps to an invalid repdigit CPF.
- **Rejected:** relaxing the uniqueness test (a duplicated CPF means a duplicated onboarding in a real bank).

## Loader

### D09. DuckDB loader with one Postgres transaction per date
- **Why:** reads only committed dates; a date is (re)loaded when its manifest SHA-256 differs from the last one in `audit.lake_loads`. Per date: delete, `INSERT ... BY NAME`, reconcile counts with the manifest, re-check the manifest, write the audit row, commit. Any failure rolls the date back.
- **Rejected:** row-by-row inserts from Python (slow) and full reloads (not incremental).

### D10. Raw tables mirror the lake; typing happens in dbt
- **Why:** Bronze stays faithful to the source; the Pandera contract versions the schema and `INSERT ... BY NAME` makes drift fail. Date-only columns stay `timestamp` in `raw` and are cast in staging.
- **Rejected:** hand-written DDL in the loader, a second schema definition to keep in sync.

### D11. Quarantine stays in the lake; only its counts reach the warehouse
- **Why:** invalid rows are evidence, not facts. The loader copies the manifest's per-entity counts to `audit.lake_loads.manifest_entities`, which feeds `dq_ingestion_daily`.
- **Rejected:** loading quarantined rows into Postgres, where they could be queried as if valid.

### D12. Index on `raw.<entity>(_reference_date)`, created idempotently by the loader
- **Why:** every load deletes and reconciles one date; without the index each date was a sequential scan of a growing table, quadratic over an 837-date backfill.
- **Rejected:** managing indexes outside the loader, which drift from the tables it creates.

## Warehouse (dbt)

### D13. Medallion layers mapped to schemas
- **Why:** Bronze = lake + `raw`; Silver = dbt `stg_` + `int_` (typing, joins, business rules, PII masking); Gold = `marts` as a star schema. One schema per layer gives clear ownership and least privilege.
- **Rejected:** a single schema, where every consumer can read unmasked data.

### D14. Unknown member (`'-1'`) instead of dropping orphans
- **Why:** row-level quarantine can drop a parent while its children survive. Facts keep every raw row and point orphans to the unknown member, so totals reconcile with the source and the gap stays visible. The model excludes these rows.
- **Rejected:** inner joins that silently shrink the facts.

### D15. PII masked in staging
- **Why:** CPF becomes a salted SHA-256 (`DBT_PII_SALT`), e-mail is reduced to its domain, phone to its area code, and the name is dropped. The customers staging model is a table so the salt never appears in a view definition.
- **Rejected:** encryption (reversible, needs key management) and masking in the marts (PII would already sit in Silver).

### D16. Two facts sharing the dimensions
- **Why:** `fct_credit_operations` (one row per contract, with the PD label) and `fct_installments` (collections and aging) have different grains.
- **Rejected:** one wide fact mixing grains, which double-counts amounts.

### D17. No imputation of the label
- **Why:** `pd_label` is NULL with a `label_status` (`immature`, `incomplete_schedule`). The rule is symmetric: a window with a quarantined installment is never labeled, even if a default was observed in it (`has_defaulted_to_date` still records it for monitoring). An earlier version labeled those defaults as 1 while dropping the goods with gaps, which kept 160 extra defaults and raised the training default rate from 11.2% to 12.3%. Delinquency and maturity use the extraction clock, never `current_date`. Window and threshold are dbt vars.
- **Rejected:** treating immature or partially observed contracts as good payers (right-censoring bias), and labeling only the defaults of incomplete windows (selection bias).

### D18. Portable SQL where a macro exists
- **Why:** `dbt_utils.date_spine` instead of `generate_series` (not supported on Redshift data tables), `dbt.dateadd` instead of `make_interval`, `SUM(CASE ...)` instead of `FILTER (WHERE ...)`, `ROW_NUMBER()` instead of `DISTINCT ON`. What remains Postgres-specific is listed in the README.
- **Rejected:** claiming full portability without running on another adapter.

### D19. Contracts at both ends
- **Why:** Pandera guards the lake and enforced dbt model contracts guard the Gold interface (types, primary keys, `NOT NULL`), so drift fails the build instead of reaching a consumer.
- **Rejected:** schema tests only, which detect drift after the table is already built.

### D20. Data-quality budgets with warn and error levels
- **Why:** a degradation shows up as a warning before it blocks. Thresholds are vars, set so that a normal run has zero warnings:
  - unknown customer: warn > 2% (about twice the observed 0.99%), error > 5%;
  - incomplete label window: warn > 15% (expected ~11% with a 1% dirty rate), error > 30%;
  - quarantine per entity: warn > 5%, error > 10%, on a rolling window of the last 7 loaded dates (`ROWS BETWEEN 6 PRECEDING AND CURRENT ROW`), evaluated once the window holds 7 dates.
- **Rejected:** pass/fail checks (ignored once they flap) and a daily quarantine rate (pure noise on ~30-row batches).

## Model

### D21. Out-of-time train / validation / test on mature cohorts only
- **Why:**
  - **Maturity limit from the data.** The last contract date mature for every product is computed from the observation clock (`min(observed_at)` of the facts): observation - 90 days - 12 months, the same parameters as the dbt label (tested against the dbt vars). For the current data that is 2025-07-17. Later contracts raise instead of being dropped silently.
  - **Three periods.** Test = the last `test_months` (3) before the limit, validation = the `validation_months` (3) before the test, train = everything earlier. Both sizes are parameters.
  - **One look at the test.** Hyperparameters, feature-exclusion evidence and permutation importance use the validation period only. The chosen models are refit on train + validation and scored once on the test period. An earlier version tuned the comparison model and judged features on the test period, which made its metrics optimistic.
- **Rejected:** a random split (leaks the future into training and hides drift) and a single train/test split used for tuning.

### D22. Explicit feature allowlist, enforced by tests
- **Why:** 15 features known at the grant date. Outcome columns (`max_dpd_in_window`, `is_mature`, `window_*`, `label_status`, `pd_label`), keys and lineage are forbidden, and a test fails if any of them reaches the feature set. The population query is generated from the allowlist, so outcome columns are never fetched.
- **Rejected:** a denylist, which lets every new column in by default.

### D23. Columns excluded on purpose, although known at the grant date
- **Why:**
  - **No feature may carry the bank's own price.** `interest_rate_monthly` comes from risk-based pricing on the bureau score: a model trained on it learns the bank's previous decisions (feedback loop) and drifts whenever pricing changes. An earlier version excluded the rate but kept `installment_amount` and `installment_to_income_ratio`. Since installment = principal x annuity(rate, term), the rate was recoverable from principal, term and installment, so that exclusion did not work. The installment, its ratio and `total_scheduled_amount` are now excluded too (`PRICE_DEPENDENT_COLUMNS`).
  - **Affordability without the price.** `reference_installment_to_income_ratio` is the installment at a fixed qualifying rate (dbt var `reference_rate_monthly` = 3% a month, at or above every payroll and vehicle contract rate) over income, in the spirit of a stress-test qualifying rate. It depends on principal, term and income only.
  - **Known residual.** For 543 of 6,681 payroll loans (8%) the source capped the principal with the 35% income-commitment rule, which uses the contract rate, so the principal itself carries some rate information for those loans. Removing it would need the requested (pre-cap) amount, which the source does not provide.
  - **Proxies.** `email_domain`, `phone_area_code` and `state_code` are socio-economic or geographic proxies; the source assigns the state independently of risk. The exclusion rests on that argument: the earlier evidence for dropping `state_code` came from the test period and is no longer used.
- **Rejected:** keeping every available column and letting the model sort it out.

### D24. Logistic regression as the main model, one comparison model
- **Why:** the scorecard standard: odds ratios and an exact per-applicant decomposition of the log-odds. Both models are tuned on the validation period (logistic C in {0.1, 1, 10}; boosting depth/leaf in {2/200, 3/100, 4/50}). Out of time the logistic model (test AUC 0.828, KS 0.519) slightly beats the boosting model (0.825, KS 0.509), with better calibration (largest decile gap 0.036 vs 0.044) and a smaller train/test gap.
- **Rejected:** shipping the boosting model (no gain, less auditable) and time-series models (the task is a cross-sectional PD at the grant date).

### D29. Explicit reference categories in the one-hot encoding
- **Why:** with every level one-hot encoded next to an intercept, category coefficients are not identifiable on their own (L2 only centres them), so an odds ratio per category meant nothing precise. Each categorical feature now drops a declared reference level, `product_type = personal_loan` (largest and riskiest product) and `employment_type = clt` (most common), and its odds ratios read "vs. the reference category". A reference applicant's categorical contribution is exactly zero.
- **Rejected:** keeping full one-hot and describing the odds ratios as "a category vs. the rest", which they are not.

### D25. Metrics: AUC, Gini, KS and calibration
- **Why:** the credit-risk standard for discrimination, plus Brier score and a calibration table by predicted-PD decile, overall and by product.
- **Rejected:** accuracy, meaningless at a ~12% default rate.

## Consumption

### D26. Read-only login roles per consumer
- **Why:** `ml_reader` and `dashboard_reader` inherit only `analytics_reader` (SELECT on marts) and default to read-only sessions; passwords come from `.env`. The model and the dashboard containers never receive the admin credentials.
- **Rejected:** connecting with the pipeline's admin role.

### D27. The dashboard reads marts only
- **Why:** pipeline health is exposed through `dq_pipeline_loads` and `dq_ingestion_daily`, so the dashboard never reads `audit`, `raw` or staging; a test fails if a dashboard query references anything outside `marts`.
- **Rejected:** querying the audit tables directly from the app.

### D28. Backfill for the model
- **Why:** 2024-07-01 to 2026-10-15 with 30 customers per batch and a 1% dirty rate, so 12-installment windows mature for every product; the row counts and load time were estimated with an exact dry run before running it.
- **Rejected:** a short history, in which only 6-month personal loans would be mature.
