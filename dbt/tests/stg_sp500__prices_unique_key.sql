-- dbt/tests/stg_sp500__prices_unique_key.sql
--
-- Singular test: assert (ticker, trade_date) is unique in the staging model.
-- Fails (returns rows) if any composite key appears more than once.
-- Complements dbt's built-in `unique` test, which only handles single columns.

select
    ticker,
    trade_date,
    count(*) as row_count

from {{ ref('stg_sp500__prices') }}
group by ticker, trade_date
having count(*) > 1