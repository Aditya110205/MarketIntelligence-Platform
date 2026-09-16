{{
    config(
        materialized = 'table',
        schema       = 'intermediate',
        tags         = ['phase5', 'intermediate']
    )
}}

-- ADR-001: forward-fill close_price per ticker; other price columns pass through.
-- Grain: one row per (ticker, trade_date). Unchanged from stg_sp500__prices.
--
-- NOTE: Postgres 18 does not implement LAST_VALUE ... IGNORE NULLS.
-- Using the portable COUNT/MAX window idiom instead:
--   grp increments each time close_price is non-null; MAX(close_price) over
--   (ticker, grp) then carries that non-null value forward until the next one.

with staged as (

    select
        trade_date,
        ticker,
        open_price,
        high_price,
        low_price,
        close_price,
        volume
    from {{ ref('stg_sp500__prices') }}

),

grouped as (

    select
        *,
        count(close_price) over (
            partition by ticker
            order by trade_date
            rows between unbounded preceding and current row
        ) as fill_grp
    from staged

),

filled as (

    select
        trade_date,
        ticker,
        open_price,
        high_price,
        low_price,

        close_price,
        close_price is null                                as close_price_was_filled,

        max(close_price) over (
            partition by ticker, fill_grp
        )                                                  as close_price_filled,

        volume
    from grouped

)

select
    trade_date,
    ticker,
    open_price,
    high_price,
    low_price,
    close_price,
    close_price_filled,
    close_price_was_filled,
    volume
from filled