
---

## KI-003 — Negative close prices survive Silver cleaning

**Severity:** Medium (metric correctness)
**Discovered:** Phase 5, Step 4 (fct_daily_metrics build)
**Status:** Open — deferred to Phase 4.5 (Silver layer hardening)

### Evidence

`public_intermediate.int_sp500__prices_enriched` contains 5 rows with
`close_price_filled < 0`, all from the raw source (`close_price_was_filled = false`):

| ticker | trade_date | close_price |
|--------|------------|-------------|
| AMZN   | 2013-07-28 | -250.93     |
| JNJ    | 2015-07-12 | -71.35      |
| MSFT   | 2014-10-07 | -12.60      |
| MSFT   | 2015-03-23 | -11.62      |
| NVDA   | 2014-05-07 | -17.63      |

Phase 3's `clean()` in `app/services/silver.py` handled NULLs but did not
validate price positivity. The negatives are almost certainly data-entry
artifacts in the source CSV (values look like they belong to a different
column, e.g. a daily change, not a price).

### Current mitigation (Phase 5)

`fct_daily_metrics.log_return` is wrapped in a `CASE` that returns NULL when
either `close_price_filled` or its `lag()` is non-positive. The 5 offending
rows therefore have NULL `log_return`.

Effect on `volatility_30d` (corrected from the initial write-up):

`stddev_samp` **skips** NULL values within its window — it does not propagate
them. So the 5 NULL log-returns do **not** cascade NULL volatility into the
following 29 rows. Measured `volatility_30d` NULL count in the built mart is
**20**, which is exactly 2 rows per ticker × 10 tickers: the first row of
each ticker has no `lag()` at all, and the second row has exactly one
`log_return`, insufficient for `stddev_samp` (needs ≥ 2 points). The 5
negative-close rows contribute zero additional NULLs to volatility.

Downstream consumers should treat NULL `volatility_30d` as "insufficient
clean data," not "zero volatility."

Row counts are preserved: `fct_daily_prices` and `fct_daily_metrics` both
remain at 947 rows, so a join on `(ticker, trade_date)` loses no rows.

### Proper fix (deferred)

In `app/services/silver.py::clean()`, add a validation step that either:
- (a) drops rows where `close <= 0`, OR
- (b) flags them with a `price_valid boolean` column that downstream models
  can filter on.

Option (b) is preferred — it preserves the audit trail. Out of scope for
Phase 5; bundle with Phase 4.5 (KI-001).

### Test to add

`dbt/tests/int_sp500__prices_enriched_negative_close_count.sql` — pins the
current count at exactly 5. If the Silver fix lands in Phase 4.5, this test
should be updated to expect 0 (and its name changed to reflect the invariant).
Pinning the count means: any *new* negative closes fail the build, while the
5 known ones are tolerated. Known-bad, frozen count.

---

## PERF-001 — dbt invocation fixed cost (Phase 5 baseline)

**Category:** Performance baseline (not a bug)
**Measured:** Phase 5, Step 6
**Context:** Full `dbt run && dbt test` chain, 4 models, 20 data tests.

### Measurements

Five consecutive invocations in one PowerShell session:

| # | Command  | Internal exec | Wall clock | Notes                        |
|---|----------|---------------|------------|------------------------------|
| 1 | dbt test | 0.93s         | 5.702s     | first invocation in session  |
| 2 | dbt test | 0.87s         | 5.716s     | same session                 |
| 3 | dbt run  | 0.76s         | 5.556s     | same session                 |
| 4 | dbt test | 0.88s         | 5.783s     | same session                 |
| 5 | dbt run  | 0.81s         | 5.756s     | same session                 |

Wall-clock range: 5.556s – 5.783s (spread 0.227s).
Internal range: 0.76s – 0.93s (spread 0.17s).

**Conclusion: no measurable cold/warm difference.** The fixed cost of a dbt
invocation on this machine is ~5.5s regardless of run order.

### Interpretation

dbt's internal execution timer measures only model/test SQL. Wall clock includes
Python startup (dbt's import graph — sqlglot, jinja2, networkx, etc.), adapter
registration, and Postgres connection handshake. Measured fixed cost on this
machine: **~5.5s per invocation**, essentially invariant across cold/warm and
run/test.

Implication for Phase 6 (Airflow): each Airflow task that shells out to dbt
pays this fixed cost. If the DAG runs hourly, that's ~2 hours/year of pure
Python startup for a project whose actual work is <1s. The levers, in priority
order:

1. **`dbt build`** — combine run + test into one invocation. Saves one ~5.5s
   call per DAG run.
2. **`dbt-rpc`** — a persistent dbt process. Eliminates the startup cost
   entirely, at the price of managing a service.
3. **Accept it.** At this project's scale, 5.5s is irrelevant.

Documented now so we don't rediscover it in Phase 6.

### Comparison to Phase 4 — revised

Phase 4 measured `scripts/load_file.py` at 4.914s cold / 0.772s warm (6.4×
spread). That spread does NOT transfer to dbt invocations: five consecutive
dbt runs in Phase 5 Step 6 showed a wall-clock spread of only 0.227s.

Interpretation: the Phase 4 spread came from Postgres bulk-INSERT behavior
(page allocation, WAL flush, index build on a cold relation), which is
genuinely cold/warm sensitive. dbt's fixed cost is Python import graph
(sqlglot, jinja2, networkx) plus adapter registration plus connection
handshake — all CPU-bound, none cache-sensitive at this timescale.

Implication for Phase 6: warming the machine is not a lever for dbt
invocation cost. The only levers are (a) fewer invocations, (b) fewer
processes. See "Interpretation" above.