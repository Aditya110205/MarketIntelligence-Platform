import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.ai.sql_guard import validate_sql, UnsafeSQLError

cases = [
    # The exact patterns that were failing — must now pass:
    ("SELECT MAX(fct_daily_prices.close_price_filled) FROM public_marts.fct_daily_prices WHERE ticker = 'MSFT'", True),
    ("SELECT AVG(fct_daily_metrics.close_price_filled) FROM public_marts.fct_daily_metrics WHERE ticker = 'AMZN'", True),
    ("SELECT fct_daily_prices.trade_date, fct_daily_prices.close_price FROM public_marts.fct_daily_prices LIMIT 10", True),
    ("SELECT fct_daily_metrics.ticker FROM public_marts.fct_daily_metrics", True),
    # Must still be rejected — actual unqualified table references:
    ("SELECT ticker FROM fct_daily_metrics", False),
    ("SELECT * FROM fct_daily_prices", False),
    # Must still be rejected — wrong schema:
    ("SELECT * FROM public.fct_daily_metrics", False),
    # Must still be allowed — fully qualified:
    ("SELECT ticker FROM public_marts.fct_daily_metrics", True),
    # Must still be rejected — JOIN with unqualified table:
    ("SELECT * FROM public_marts.fct_daily_metrics JOIN fct_daily_prices ON 1=1", False),
]

failures = 0
for sql, should_pass in cases:
    try:
        validate_sql(sql)
        ok = should_pass
        rule = ""
    except UnsafeSQLError as e:
        ok = not should_pass
        rule = e.rule
    if not ok:
        failures += 1
    status = "PASS" if ok else "FAIL"
    print(f"{status}  expected_pass={should_pass}  {rule:20s}  {sql[:80]}")

print(f"\n{len(cases) - failures}/{len(cases)}")
sys.exit(1 if failures else 0)