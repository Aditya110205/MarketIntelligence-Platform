-- dbt/tests/stg_sp500__prices_null_drift.sql
--
-- Singular test: assert the null counts in the staging model do not exceed
-- the numbers observed after Phase 3 cleaning:
--     open=12, high=5, low=8, close=9, volume=36
-- Fails if ANY null count increases — that's the regression signal.
--
-- We tolerate the existing nulls today (Phase 5 will decide policy).
-- We do NOT tolerate growth.

with null_counts as (

    select
        sum(case when open_price  is null then 1 else 0 end) as open_nulls,
        sum(case when high_price  is null then 1 else 0 end) as high_nulls,
        sum(case when low_price   is null then 1 else 0 end) as low_nulls,
        sum(case when close_price is null then 1 else 0 end) as close_nulls,
        sum(case when volume      is null then 1 else 0 end) as volume_nulls

    from {{ ref('stg_sp500__prices') }}

)

select *
from null_counts
where open_nulls   > 12
   or high_nulls   > 5
   or low_nulls    > 8
   or close_nulls  > 9
   or volume_nulls > 36