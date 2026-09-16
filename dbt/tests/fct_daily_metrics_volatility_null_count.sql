-- Step 5: pin the volatility_30d NULL count at 20.
--
-- 20 = 2 rows per ticker * 10 tickers. The first row of each ticker has no
-- lag() (no log_return), and the second row has exactly one log_return, which
-- is insufficient for stddev_samp (needs >= 2). From row 3 onward volatility
-- is defined (possibly computed on a partial window of 2..30 returns).
--
-- This test is a ratchet, not a verdict. If Phase 4.5 (Silver hardening)
-- removes the 5 negative closes from KI-003, this count may change (negatives
-- with no lag would add more NULLs — but they currently *don't* because
-- stddev_samp skips NULLs in the window). If the underlying data refresh
-- changes the null pattern, this test fails and the response is:
--   1. Investigate why the count changed.
--   2. Update the expected value AND this comment to reflect the new reason.

with null_count as (
    select count(*) as n
    from {{ ref('fct_daily_metrics') }}
    where volatility_30d is null
)

select n
from null_count
where n <> 20