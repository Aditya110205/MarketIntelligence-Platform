-- dbt/models/staging/stg_sp500__prices.sql
--
-- First staging model. Reads the Silver source declared in _sources.yml
-- and produces a typed, renamed, thin view.
--
-- TICKER NORMALIZATION (Phase 4 decision):
--   Silver contains raw ticker strings with two kinds of dirt:
--     - case inconsistency:  'aapl', 'AAPL'
--     - trailing-dot marker: 'AAPL.', 'AMZN.', 'FB.', ...
--   We normalize at staging via upper(trim(trailing '.' from ticker)).
--   Measured result: 27 raw variants collapse to 10 canonical tickers,
--   with ZERO (ticker, date) collisions — verified before writing this model.
--   The underlying Silver bug is logged in docs/known_issues.md (Phase 4.5).
--
-- Natural key: (ticker, trade_date). Verified unique by tests in _staging.yml.
--
-- Source schema (Silver, 947 rows, 7 columns):
--   date    date
--   open    double
--   high    double
--   low     double
--   close   double
--   volume  long
--   ticker  string

with source as (

    select * from {{ source('silver', 'sp500_clean') }}

),

renamed as (

    select
        cast(date    as date)                              as trade_date,
        upper(trim(trailing '.' from cast(ticker as text))) as ticker,
        cast(open    as double precision)                  as open_price,
        cast(high    as double precision)                  as high_price,
        cast(low     as double precision)                  as low_price,
        cast(close   as double precision)                  as close_price,
        cast(volume  as bigint)                            as volume

    from source

)

select * from renamed