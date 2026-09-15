# Known Issues / Tech Debt

Tracked issues that we deliberately did NOT fix inline, with owner phase
and rationale. Format: one section per issue.

---

## KI-001 — Silver ticker column is not normalized

**Discovered:** Phase 4, during dbt staging tests.
**Symptom:** `public.silver_sp500_clean.ticker` contains 27 distinct raw
strings that represent only 10 logical tickers, due to two dirt patterns:
- case inconsistency (e.g. `aapl` vs `AAPL`)
- trailing-dot marker (e.g. `AAPL.`, `AMZN.`) — likely a CSV footnote artifact

**Measured evidence (Phase 4 audit):**
- Raw distinct strings: 27
- Canonical distinct tickers after `upper(trim(trailing '.' from ticker))`: 10
- Collisions on `(canonical_ticker, date)` after normalization: 0
- Full variant map captured in the audit output of
  `scripts/_check_ticker_normalization.py`.

**Current mitigation (Phase 4):**
Normalization is done in `dbt/models/staging/stg_sp500__prices.sql` via
`upper(trim(trailing '.' from ticker))`. Tests in
`dbt/tests/stg_sp500__prices_canonical_tickers.sql` lock the canonical set
to the 10 measured values, so any new dirt pattern fails the test suite.

**Proper fix (deferred to Phase 4.5):**
Move normalization into `app/services/silver.py`'s `clean()` function, so
Silver output is canonical and staging doesn't have to clean anything.
Requires:
1. Add `upper(trim(trailing '.' from ticker))`-equivalent to the Spark
   cleaner.
2. Re-run `scripts/run_silver.py`.
3. Re-run `scripts/load_file.py`.
4. Re-measure row count and the Phase 3 runtime baseline (currently ~21.5 s).
5. Remove normalization from staging (or leave as defense-in-depth — decide then).

**Owner:** Phase 4.5 (self-contained Silver revisit).
**Why not fixed now:** 80/20 rule — Phase 4 goal is dbt staging green, not
Phase 3 correctness. Fix is captured, tested at the boundary, and tracked.