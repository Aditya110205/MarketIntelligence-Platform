{{
    config(
        materialized = 'table',
        schema       = 'marts',
        tags         = ['phase5', 'marts']
    )
}}

-- Grain: one row per (ticker, trade_date).
-- Purpose: BI/API-facing rolling metrics fact. Reads from the intermediate
-- layer so that ADR-001 (ffill close_price) is inherited, not re-applied.
--
-- Metrics:
--   ma7              — 7-observation rolling mean of close_price_filled
--   ma30             — 30-observation rolling mean of close_price_filled
--   volatility_30d   — annualized 30-observation rolling stddev of daily log returns
--   drawdown         — close_price_filled / running peak - 1  (always <= 0)
--
-- All windows use ROWS (physical observations), not RANGE (date-units), per
-- the finance convention that "MA7" means "last 7 observations."
--
-- KI-003: raw source has 5 rows with negative close_price (AMZN 2013-07-28,
-- JNJ 2015-07-12, MSFT 2014-10-07, MSFT 2015-03-23, NVDA 2014-05-07). log_return
-- is NULL on those rows and on the rows immediately following them (because
-- lag() of a negative value propagates). See docs/known_issues.md.

with enriched as (

    select
        trade_date,
        ticker,
        close_price_filled
    from {{ ref('int_sp500__prices_enriched') }}

),

with_returns as (

    select
        trade_date,
        ticker,
        close_price_filled,

        -- daily log return: ln(close_t / close_{t-1}) within ticker.
        -- NULL when either side is non-positive (KI-003) or when there is
        -- no prior row (first observation per ticker).
        case
            when close_price_filled > 0
             and lag(close_price_filled) over (
                    partition by ticker
                    order by trade_date
                 ) > 0
            then ln(
                close_price_filled
                / lag(close_price_filled) over (
                    partition by ticker
                    order by trade_date
                )
            )
            else null
        end as log_return

    from enriched

),

metrics as (

    select
        trade_date,
        ticker,
        close_price_filled,

        avg(close_price_filled) over (
            partition by ticker
            order by trade_date
            rows between 6 preceding and current row
        ) as ma7,

        avg(close_price_filled) over (
            partition by ticker
            order by trade_date
            rows between 29 preceding and current row
        ) as ma30,

        stddev_samp(log_return) over (
            partition by ticker
            order by trade_date
            rows between 29 preceding and current row
        ) * sqrt(252) as volatility_30d,

        close_price_filled
          / max(close_price_filled) over (
                partition by ticker
                order by trade_date
                rows between unbounded preceding and current row
            )
          - 1 as drawdown

    from with_returns

)

select
    trade_date,
    ticker,
    close_price_filled,
    ma7,
    ma30,
    volatility_30d,
    drawdown
from metrics