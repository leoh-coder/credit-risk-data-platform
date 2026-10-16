-- Open Finance history is shared BEFORE the application. A transaction on/after the
-- application date means the source changed semantics; the feature model filters such rows, but the
-- source must be fixed, so this fails loudly instead of being silently absorbed.

select
    t.transaction_id,
    t.customer_id,
    t.transacted_at,
    c.registration_date
from {{ ref('stg_open_finance__transactions') }} as t
inner join {{ ref('stg_core_banking__customers') }} as c using (customer_id)
where t.transacted_at >= c.registration_date
