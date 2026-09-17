"""
app/api/main.py

FastAPI application for the Market Intelligence platform.

Phase 7 scope:
    /health          - liveness + DB reachability check
    /metrics         - aggregate metrics across all tickers
    /ticker/{symbol} - per-ticker time series over a date range

Phase 9 Step 5 addition:
    /ask             - natural-language question -> SQL -> rows -> explanation
                       (registered via app.api.ask router)

Run locally:
    uvicorn app.api.main:app --reload --host 127.0.0.1 --port 8000

OpenAPI docs:
    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.database.session import get_engine
from app.api.ask import router as ask_router

logger = logging.getLogger("market_intel.api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    db: str


class MoverItem(BaseModel):
    """A single ticker's latest 1-day move."""
    ticker: str
    close_price: float = Field(..., description="Latest close (always > 0; KI-003 filtered).")
    return_1d: Optional[float] = Field(
        None, description="1-day return; positive for gainers, negative for losers."
    )


class MetricsResponse(BaseModel):
    """
    Aggregate metrics as of the latest available data.

    `top_gainers` and `top_losers` contain only tickers whose latest
    1-day return is strictly positive / strictly negative, respectively.
    Lists may be shorter than `top_n` if fewer tickers qualify.
    """
    as_of_date: date
    tickers_count: int
    rows_count: int
    top_gainers: list[MoverItem] = Field(default_factory=list)
    top_losers: list[MoverItem] = Field(default_factory=list)


class TickerPoint(BaseModel):
    trade_date: date
    close_price: float
    ma7: Optional[float] = None
    ma30: Optional[float] = None
    volatility_30d: Optional[float] = None
    drawdown: Optional[float] = None


class TickerResponse(BaseModel):
    symbol: str
    points_count: int
    points: list[TickerPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("market_intel API starting")
    yield
    logger.info("market_intel API shutting down")


app = FastAPI(
    title="Market Intelligence API",
    description="Analytics API over the S&P 500 warehouse (Bronze -> Silver -> dbt marts).",
    version="0.4.0",
    lifespan=lifespan,
)

# Phase 9 Step 5: register the /ask router. This must come AFTER `app` is
# instantiated and BEFORE the first request is served.
app.include_router(ask_router)


# ---------------------------------------------------------------------------
# SQL (parameterized)
# ---------------------------------------------------------------------------
# Schema note: dbt is configured with custom per-folder schemas in
# dbt_project.yml, so marts land in `public_marts` (target_schema + "_marts"),
# not in `public`. See dbt/profiles.yml (`schema: public`) and
# dbt/dbt_project.yml (`+schema: marts` under models/marts).
# Do not "simplify" these to unqualified names — that relies on search_path
# and hides the dependency.
#
# Column note: fct_daily_metrics has no `return_1d` column. 1-day return is
# computed inline via a window function (LAG over trade_date). If Phase 10
# optimizes this, the winning move is to add return_1d as a materialized
# column in the dbt model, not to optimize the window function.
#
# Ticker alignment: tickers do NOT share a common latest trade_date in this
# dataset (range: 2015-09-13 to 2015-09-27, verified 2026-09-16). "Top movers"
# therefore means "each ticker's most recent 1-day return", implemented via
# ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY trade_date DESC).
#
# KI-003: 5 rows have negative close_price_filled; filtered with
# close_price_filled > 0. Proper fix is Phase 4.5.
#
# close_price_was_filled lives in fct_daily_prices, not fct_daily_metrics.
# The /ticker endpoint omits it for now; adding it requires a JOIN and is
# deferred to Phase 7.5.

SQL_HEALTH = text("SELECT 1")

SQL_METRICS_SUMMARY = text(
    """
    SELECT
        MAX(m.trade_date)         AS as_of_date,
        COUNT(DISTINCT m.ticker)  AS tickers_count,
        COUNT(*)                  AS rows_count
    FROM public_marts.fct_daily_metrics m
    """
)

SQL_TOP_MOVERS = text(
    """
    WITH ranked AS (
        SELECT
            m.ticker,
            m.trade_date,
            m.close_price_filled              AS close_price,
            (m.close_price_filled - LAG(m.close_price_filled)
                OVER (PARTITION BY m.ticker ORDER BY m.trade_date))
              / NULLIF(LAG(m.close_price_filled)
                OVER (PARTITION BY m.ticker ORDER BY m.trade_date), 0)
                                              AS return_1d,
            ROW_NUMBER() OVER (
                PARTITION BY m.ticker ORDER BY m.trade_date DESC
            )                                 AS rn
        FROM public_marts.fct_daily_metrics m
        WHERE m.close_price_filled > 0
    )
    SELECT ticker, close_price, return_1d
    FROM ranked
    WHERE rn = 1
      AND return_1d IS NOT NULL
      AND return_1d {sign}
    ORDER BY return_1d {direction}
    LIMIT :n
    """
)

SQL_TICKER_SERIES = text(
    """
    SELECT
        m.trade_date              AS trade_date,
        m.close_price_filled      AS close_price,
        m.ma7,
        m.ma30,
        m.volatility_30d,
        m.drawdown
    FROM public_marts.fct_daily_metrics m
    WHERE m.ticker = :symbol
      AND m.trade_date >= :start_date
      AND m.trade_date <= :end_date
    ORDER BY m.trade_date ASC
    LIMIT :limit
    """
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness + DB reachability."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(SQL_HEALTH)
    except Exception as e:
        logger.exception("health check failed")
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "db": "error", "detail": str(e)},
        )
    return HealthResponse(status="ok", db="ok")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@app.get("/metrics", response_model=MetricsResponse)
def metrics(
    top_n: int = Query(default=5, ge=1, le=20, description="Number of top gainers/losers to return."),
) -> MetricsResponse:
    """
    Aggregate metrics across all tickers.

    - `as_of_date` = the latest trade_date across all tickers (not shared
      by every ticker; see SQL note above).
    - `top_gainers` = tickers with positive latest 1-day return, ranked DESC.
    - `top_losers` = tickers with negative latest 1-day return, ranked ASC.
    - Each list is capped at `top_n` and may be shorter if fewer tickers
      qualify. A ticker cannot appear in both lists.

    1-day return is computed from close_price_filled via LAG (there is no
    return_1d column in fct_daily_metrics).
    """
    engine = get_engine()
    try:
        with engine.connect() as conn:
            summary = conn.execute(SQL_METRICS_SUMMARY).mappings().one()

            gainers = conn.execute(
                text(
                    str(SQL_TOP_MOVERS)
                    .replace("{direction}", "DESC")
                    .replace("{sign}", "> 0")
                ),
                {"n": top_n},
            ).mappings().all()

            losers = conn.execute(
                text(
                    str(SQL_TOP_MOVERS)
                    .replace("{direction}", "ASC")
                    .replace("{sign}", "< 0")
                ),
                {"n": top_n},
            ).mappings().all()
    except Exception as e:
        logger.exception("metrics query failed")
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if summary["as_of_date"] is None:
        raise HTTPException(status_code=404, detail="no metrics data available")

    return MetricsResponse(
        as_of_date=summary["as_of_date"],
        tickers_count=summary["tickers_count"],
        rows_count=summary["rows_count"],
        top_gainers=[MoverItem(**dict(r)) for r in gainers],
        top_losers=[MoverItem(**dict(r)) for r in losers],
    )


# ---------------------------------------------------------------------------
# Ticker
# ---------------------------------------------------------------------------

@app.get("/ticker/{symbol}", response_model=TickerResponse)
def ticker(
    symbol: str = Path(..., min_length=1, max_length=10, description="Ticker symbol, e.g. AAPL."),
    start_date: Optional[date] = Query(default=None, description="Inclusive start date (YYYY-MM-DD)."),
    end_date: Optional[date] = Query(default=None, description="Inclusive end date (YYYY-MM-DD)."),
    limit: int = Query(default=500, ge=1, le=5000, description="Max rows returned."),
) -> TickerResponse:
    """
    Per-ticker daily time series with MA7, MA30, volatility_30d, and drawdown.

    Dates are inclusive. If start_date/end_date are omitted, the entire
    available range for the ticker is returned (up to `limit` rows).
    """
    sym = symbol.upper()

    if start_date is None:
        start_date = date(1900, 1, 1)
    if end_date is None:
        end_date = date(2100, 1, 1)

    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date must be <= end_date")

    engine = get_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                SQL_TICKER_SERIES,
                {
                    "symbol": sym,
                    "start_date": start_date,
                    "end_date": end_date,
                    "limit": limit,
                },
            ).mappings().all()
    except Exception as e:
        logger.exception("ticker query failed")
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if not rows:
        raise HTTPException(status_code=404, detail=f"no data for symbol {sym} in range")

    return TickerResponse(
        symbol=sym,
        points_count=len(rows),
        points=[TickerPoint(**dict(r)) for r in rows],
    )