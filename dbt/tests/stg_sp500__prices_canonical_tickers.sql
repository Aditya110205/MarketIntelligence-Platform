-- dbt/tests/stg_sp500__prices_canonical_tickers.sql
--
-- Singular test: the set of tickers in the staging model must be EXACTLY
-- the 10 canonical values measured from silver on 2026-09-15 (Phase 4 audit):
--     AAPL, AMZN, FB, GOOGL, JNJ, JPM, MSFT, NVDA, PG, V
--
-- Fails if a ticker is MISSING (data loss) or a NEW one APPEARS (scope change
-- or new dirt pattern). Either way, the response is a deliberate decision,
-- not silent drift.
--
-- Re-derive the expected set with:
--   SELECT DISTINCT upper(trim(trailing '.' from ticker))
--   FROM public.silver_sp500_clean ORDER BY 1;

with actual as (

    select distinct ticker
    from {{ ref('stg_sp500__prices') }}

),

expected as (

    select unnest(array[
        'AAPL','AMZN','FB','GOOGL','JNJ','JPM','MSFT','NVDA','PG','V'
    ]) as ticker

),

mismatches as (

    (select 'unexpected_ticker' as reason, ticker from actual
     except
     select 'unexpected_ticker' as reason, ticker from expected)

    union all

    (select 'missing_ticker' as reason, ticker from expected
     except
     select 'missing_ticker' as reason, ticker from actual)

)

select * from mismatches