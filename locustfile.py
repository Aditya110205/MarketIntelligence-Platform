"""
locustfile.py

Load test for the Market Intelligence API (Phase 7).

Run:
    # Web UI (interactive; open http://localhost:8089)
    locust -f locustfile.py --host http://127.0.0.1:8000

    # Headless (reproducible; writes CSVs)
    locust -f locustfile.py --host http://127.0.0.1:8000 \
           --headless -u 50 -r 5 -t 30s \
           --csv data/perf/phase7_baseline

Endpoints exercised:
    /health          weight 1  (cheap DB ping)
    /metrics         weight 2  (heaviest — window function over full table)
    /ticker/{sym}    weight 5  (typical read)

The weights approximate a realistic mix where per-ticker reads dominate.

Note on honesty: this runs on the same machine as uvicorn, so latencies
include CPU contention between Locust and the API. Phase 10 will use the
same setup, so the *delta* is still meaningful; absolute numbers are not
production-representative.
"""

from __future__ import annotations

import random

from locust import HttpUser, between, task

# A small set of real tickers from the dataset. Randomizing avoids the OS
# page cache giving one symbol an unfair advantage.
TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "JNJ", "PG", "FB", "V"]


class MarketIntelUser(HttpUser):
    """One virtual user exercising the market intelligence API."""

    # Think time between requests: 100-500 ms. Prevents thundering-herd
    # behavior and mimics a real interactive client.
    wait_time = between(0.1, 0.5)

    @task(1)
    def health(self) -> None:
        self.client.get("/health", name="/health")

    @task(2)
    def metrics(self) -> None:
        self.client.get("/metrics", name="/metrics")

    @task(5)
    def ticker(self) -> None:
        symbol = random.choice(TICKERS)
        # Use the same "name" for all symbols so Locust aggregates them
        # into one row. Without this, each ticker would appear separately.
        self.client.get(f"/ticker/{symbol}", name="/ticker/[symbol]")