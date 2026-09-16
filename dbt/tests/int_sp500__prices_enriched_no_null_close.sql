-- ADR-001 enforcement: close_price_filled must be non-null for every row
-- EXCEPT the first row of each ticker's series (where there is no prior value
-- to carry forward).
--
-- This catches the failure mode we explicitly rejected: "leave NULL and let
-- window functions skip". If ffill is ever removed or broken, this test fails
-- on the second-and-later rows of each ticker.

with first_row_per_ticker as (

    select
        ticker,
        min(trade_date) as first_trade_date
    from {{ ref('int_sp500__prices_enriched') }}
    group by ticker

),

violations as (

    select
        i.ticker,
        i.trade_date
    from {{ ref('int_sp500__prices_enriched') }} i
    join first_row_per_ticker f
      on i.ticker = f.ticker
    where i.close_price_filled is null
      and i.trade_date > f.first_trade_date

)

select *
from violations