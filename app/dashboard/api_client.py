"""
Phase 8 — HTTP client for the Market Intelligence FastAPI.

The ONLY file in the project that knows the API base URL.
Everything downstream receives parsed dicts and never touches HTTP.
"""

from __future__ import annotations

import os
from typing import Any

import requests

# ---- config ---------------------------------------------------------------

API_BASE_URL = os.environ.get("MARKET_INTEL_API_URL", "http://127.0.0.1:8000")
DEFAULT_TIMEOUT = 5  # seconds — smallest fix that prevents a hung dashboard

# ---- error type -----------------------------------------------------------

class APIError(Exception):
    """Normalized failure from any api_client call.

    Attributes:
        message:     human-readable description, safe to show in the UI.
        status_code: HTTP status if the server responded, else None
                     (None means connection refused / timeout / bad JSON).
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __repr__(self) -> str:
        return f"APIError(status_code={self.status_code!r}, message={self.message!r})"


# ---- internal helpers -----------------------------------------------------

def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET {API_BASE_URL}{path} with params, return parsed JSON.

    Raises APIError on any failure — never leaks requests exceptions.
    """
    url = f"{API_BASE_URL}{path}"
    try:
        resp = requests.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except requests.ConnectionError as e:
        raise APIError(
            f"Cannot reach API at {API_BASE_URL}. Is uvicorn running?",
            status_code=None,
        ) from e
    except requests.Timeout as e:
        raise APIError(
            f"API timed out after {DEFAULT_TIMEOUT}s at {url}",
            status_code=None,
        ) from e
    except requests.RequestException as e:
        # catch-all for anything else requests raises
        raise APIError(f"Request to {url} failed: {e}", status_code=None) from e

    if resp.status_code >= 400:
        # FastAPI error bodies look like {"detail": "..."} — surface that if present
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise APIError(
            f"API returned {resp.status_code}: {detail}",
            status_code=resp.status_code,
        )

    try:
        return resp.json()
    except ValueError as e:
        raise APIError(
            f"API returned non-JSON body from {url}: {resp.text[:200]}",
            status_code=resp.status_code,
        ) from e


# ---- public API -----------------------------------------------------------

def get_health() -> dict[str, Any]:
    """GET /health -> {"status": "ok", "db": "ok"}"""
    return _get("/health")


def get_metrics(top_n: int = 5) -> dict[str, Any]:
    """GET /metrics?top_n=N.

    Returns a dict with keys:
        as_of_date, tickers_count, rows_count, top_gainers, top_losers
    Each gainer/loser item has at minimum: symbol, close_price, return_1d.
    """
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    return _get("/metrics", params={"top_n": top_n})


def get_ticker(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """GET /ticker/{symbol}?start_date=&end_date=&limit=.

    Returns a dict with keys: symbol, points_count, points (list of dicts).
    Each point has: trade_date, close_price, ma7, ma30, volatility_30d, drawdown.
    """
    if not symbol:
        raise ValueError("symbol is required")
    params: dict[str, Any] = {"limit": limit}
    if start_date is not None:
        params["start_date"] = start_date
    if end_date is not None:
        params["end_date"] = end_date
    return _get(f"/ticker/{symbol}", params=params)