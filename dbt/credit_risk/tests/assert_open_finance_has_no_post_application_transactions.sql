-- Open Finance history precedes the application. The feature model filters later rows anyway, but a
-- transaction on/after the application date means the source changed semantics, so fail loudly.

select
    t.transaction_id,
    t.customer_id,
    t.transacted_at,
    c.registration_date
from {{ ref('stg_open_finance__transactions') }} as t
inner join {{ ref('stg_core_banking__customers') }} as c using (customer_id)
where t.transacted_at >= c.registration_date
