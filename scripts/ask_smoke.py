"""
Phase 9 Step 5 smoke test: hit POST /ask with three questions and print
the full response for each (SQL, row count, first row, latency split,
explanation).

Run with the API live on 127.0.0.1:8000:
    python scripts/ask_smoke.py
"""

import sys
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


API_BASE = "http://127.0.0.1:8000"

QUESTIONS = [
    "What is the average 30-day volatility for AAPL in 2014?",
    "Show me MSFT's drawdown on every Friday in September 2015.",
    "What were the top 5 gainers on 2015-09-27?",
]


def _print_separator(char: str = "=", width: int = 78) -> None:
    print(char * width)


def main() -> int:
    # Sanity: is the API alive at all?
    try:
        h = requests.get(f"{API_BASE}/health", timeout=5)
    except requests.RequestException as e:
        print(f"API not reachable at {API_BASE}: {e}")
        print("Start it with: uvicorn app.api.main:app --host 127.0.0.1 --port 8000")
        return 1
    if h.status_code != 200:
        print(f"/health returned HTTP {h.status_code}: {h.text}")
        return 1
    print(f"/health OK: {h.json()}\n")

    failures = 0
    for i, q in enumerate(QUESTIONS, 1):
        _print_separator()
        print(f"Q{i}: {q}")
        _print_separator("-")
        try:
            r = requests.post(
                f"{API_BASE}/ask",
                json={"question": q},
                timeout=180,
            )
        except requests.RequestException as e:
            print(f"REQUEST FAILED: {e}\n")
            failures += 1
            continue

        if r.status_code != 200:
            print(f"HTTP {r.status_code}")
            try:
                print(f"detail: {r.json().get('detail')}")
            except Exception:
                print(f"body: {r.text[:800]}")
            print()
            failures += 1
            continue

        body = r.json()
        print(f"SQL:\n{body['sql']}\n")
        print(f"row_count: {body['row_count']}")
        if body["rows"]:
            print(f"first row: {body['rows'][0]}")
        print(f"latency_ms: {body['latency_ms']}")
        print(f"explanation:\n{body['explanation']}\n")

    _print_separator()
    print(f"{len(QUESTIONS) - failures}/{len(QUESTIONS)} questions answered with HTTP 200")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())