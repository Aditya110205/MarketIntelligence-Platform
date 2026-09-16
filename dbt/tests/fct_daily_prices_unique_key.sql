-- Composite (ticker, trade_date) uniqueness for fct_daily_prices.

select
    ticker,
    trade_date,
    count(*) as n
from {{ ref('fct_daily_prices') }}
group by ticker, trade_date
having count(*) > 1