{% docs observation_clock %}
The moment the source extract was taken (`_extracted_at` in the lake, frozen by the ingestion `--as-of`).
Every time-dependent rule (days past due, maturity) is measured against it, never against `current_date`,
so rebuilding the warehouse tomorrow does not rewrite history.
{% enddocs %}

{% docs days_past_due %}
Days past due (DPD) of an installment at the observation clock:
paid -> how late the observed payment was (early = 0); unpaid and due -> observation date minus due date;
unpaid and not yet due -> 0 (nothing owed yet, which is NOT evidence of good behavior).
{% enddocs %}

{% docs pd_label %}
Probability-of-default target. 1 = DPD >= `pd_default_dpd` (90) on any of the first
`pd_window_installments` (12, or fewer for shorter contracts) installments; 0 = the whole window was
observed without reaching it; NULL = not labelable yet, see `label_status`.
Only mature contracts are labeled, so recent cohorts never over-represent early defaults.
{% enddocs %}

{% docs label_status %}
Why `pd_label` is (or is not) set: `labeled`; `immature` (the window plus the DPD threshold has not
elapsed at the observation clock); `incomplete_schedule` (an installment of the window was quarantined
in the lake, so a good outcome cannot be proven; an already observed default is still labeled 1).
{% enddocs %}

{% docs unknown_member %}
Unknown member (key `'-1'`). Row-level quarantine in the lake can drop a parent (customer,
operation) while its children survive; facts then point here instead of disappearing, so totals keep
reconciling with the source. Exclude it from model training and from per-customer analyses.
{% enddocs %}

{% docs surrogate_key %}
Hashed surrogate key (md5 of the natural key via dbt_utils): deterministic, so it survives full
rebuilds, unlike database sequences.
{% enddocs %}

{% docs date_key %}
Integer date key `YYYYMMDD`, joins to `dim_date.date_key`.
{% enddocs %}
