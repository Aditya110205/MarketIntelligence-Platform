{{
    config(
        materialized = 'table',
        schema       = 'marts',
        tags         = ['phase5', 'marts']
    )
}}

-- Grain: one row per (ticker, trade_date).
-- Purpose: BI/API-facing daily prices fact. Pass-through from the intermediate
-- layer; the intermediate layer is where ADR-001 (ffill close_price) is applied.
--
-- Explicit column list (no `select *`): the columns below are the mart's
-- contract. Adding a column here is a conscious change, not an upstream side
-- effect.

with enriched as (

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
    from {{ ref('int_sp500__prices_enriched') }}

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
from enriched