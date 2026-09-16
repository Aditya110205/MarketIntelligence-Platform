Concretely, in `dbt/models/intermediate/int_sp500__prices_enriched.sql`:

1. `COUNT(close_price) OVER (PARTITION BY ticker ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)` creates a group number `fill_grp` that increments each time a non-null `close_price` is seen.
2. `MAX(close_price) OVER (PARTITION BY ticker, fill_grp)` then carries that non-null value forward until the next non-null close appears, producing `close_price_filled`.
3. A companion boolean `close_price_was_filled = (close_price IS NULL)` records which rows were synthesised, so audits can still find them.
4. All Phase 5 metrics use `close_price_filled`, never raw `close_price`.

Note: `LAST_VALUE(...) IGNORE NULLS` was the original plan, but PostgreSQL 18 does not implement `IGNORE NULLS` for window functions (only the SQL standard defines it; Postgres behaves as `RESPECT NULLS`). The `COUNT`/`MAX` idiom above is the portable equivalent and works on Postgres 9.4+.