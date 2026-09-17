"""
Phase 9 — Step 2: schema context for NL→SQL.

Reads column metadata for the two marts from Postgres's information_schema,
merges it with hand-written descriptions, and returns a single string that
Step 3's prompt embeds as RAG context.

Design notes:
  - Columns come from information_schema (always true by construction).
  - Descriptions come from a dict below (Postgres has no COMMENT ON COLUMN
    in this project, and editing the DB is out of scope for Phase 9).
  - Cached with lru_cache: schema changes per-migration, not per-request.
  - The "must not" rules are duplicated here AND in the Step 3 system prompt.
    Belt and suspenders against prompt-injection in the user question.
  - return_1d is NOT a column in either mart. The API computes it at query
    time with a window function (see app/api/main.py line 155). The Rules
    block below teaches the LLM the same pattern verbatim.

Run standalone to inspect the context string:
    python -m app.ai.schema_context
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import text

from app.database.session import get_engine


# ---- allow-list of tables the LLM may query -------------------------------

ALLOWED_TABLES = (
    "public_marts.fct_daily_metrics",
    "public_marts.fct_daily_prices",
)


# ---- hand-written descriptions --------------------------------------------
# Format: { "schema.table": {"__table__": "...", "column": "..."} }
# Only columns that need disambiguation or business meaning get a description.
# Everything else is documented by its column name alone, which is fine.

TABLE_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "public_marts.fct_daily_metrics": {
        "__table__": (
            "One row per (ticker, trade_date). Daily market metrics for each "
            "of the 10 tracked tickers: filled close price, moving averages, "
            "30-day rolling volatility, and drawdown from peak. This is the "
            "table to use for questions about prices (via close_price_filled), "
            "moving averages, volatility, or drawdown. NOTE: this table has "
            "NO return_1d column — 1-day return must be computed with a window "
            "function (see Rules)."
        ),
        "ticker": "Stock symbol, e.g. 'AAPL', 'MSFT'. Uppercase text.",
        "trade_date": "Trading day. DATE column, 2013-01-02 through 2015-09-27.",
        "close_price_filled": (
            "Closing price in USD, forward-filled across non-trading gaps. "
            "This is the price column to use in this table (there is no plain "
            "close_price column here)."
        ),
        "ma7": "7-day moving average of close_price_filled. NULL for first 6 days per ticker.",
        "ma30": "30-day moving average of close_price_filled. NULL for first 29 days per ticker.",
        "volatility_30d": (
            "30-day annualized rolling volatility as a decimal. NULL for the "
            "first 2 days per ticker (Phase 5 ratchet)."
        ),
        "drawdown": (
            "Drawdown from running peak as a decimal <= 0. E.g. -0.15 means "
            "15% below the highest close_price_filled seen up to that day."
        ),
    },
    "public_marts.fct_daily_prices": {
        "__table__": (
            "One row per (ticker, trade_date). Raw OHLCV plus two filled "
            "close-price columns for each of the 10 tracked tickers. Use this "
            "table for questions about open/high/low, volume, or the raw "
            "unfilled close. For most price questions, prefer "
            "fct_daily_metrics.close_price_filled — it's the canonical price "
            "used by the rest of the pipeline."
        ),
        "ticker": "Stock symbol, e.g. 'AAPL', 'MSFT'. Uppercase text.",
        "trade_date": "Trading day. DATE column, 2013-01-02 through 2015-09-27.",
        "open_price": "Opening price in USD, raw (not filled).",
        "high_price": "Intraday high price in USD, raw (not filled).",
        "low_price": "Intraday low price in USD, raw (not filled).",
        "close_price": (
            "Raw closing price in USD as ingested, may be NULL on gaps. "
            "Prefer close_price_filled for analysis."
        ),
        "close_price_filled": (
            "Closing price forward-filled across gaps. Use this when you need "
            "a continuous close series in fct_daily_prices."
        ),
        "close_price_was_filled": (
            "Boolean: TRUE if close_price was NULL and close_price_filled was "
            "imputed, FALSE if the raw value was already present. Useful to "
            "exclude imputed rows from analyses that require real trades."
        ),
        "volume": "Shares traded that day, bigint.",
    },
}


# ---- the query ------------------------------------------------------------
# Explicit table_schema filter. No search_path. Returns every column of every
# allowed table, in a stable order so diffs between runs are meaningful.

_SCHEMA_QUERY = text(
    """
    SELECT
        table_schema,
        table_name,
        column_name,
        data_type,
        is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'public_marts'
      AND table_name IN ('fct_daily_metrics', 'fct_daily_prices')
    ORDER BY table_name, ordinal_position
    """
)


def _fetch_columns() -> dict[str, list[tuple[str, str, bool]]]:
    """Return { 'public_marts.fct_...': [(col, dtype, nullable), ...] }."""
    engine = get_engine()
    out: dict[str, list[tuple[str, str, bool]]] = {}
    with engine.connect() as conn:
        for row in conn.execute(_SCHEMA_QUERY):
            full = f"{row.table_schema}.{row.table_name}"
            out.setdefault(full, []).append(
                (row.column_name, row.data_type, row.is_nullable == "YES")
            )
    return out


# ---- the builder ----------------------------------------------------------

@lru_cache(maxsize=1)
def build_schema_context() -> str:
    """Return a single formatted string describing the allowed tables.

    Cached: schema does not change per-request. If you migrate the marts,
    restart the process (or call `build_schema_context.cache_clear()`).
    """
    columns_by_table = _fetch_columns()
    missing = [t for t in ALLOWED_TABLES if t not in columns_by_table]
    if missing:
        raise RuntimeError(
            f"Schema context requested but tables not found in Postgres: {missing}. "
            f"Run `dbt build` from the repo root first."
        )

    lines: list[str] = []
    lines.append("=== Queryable tables (ONLY these may appear in generated SQL) ===")
    lines.append("")

    for table in ALLOWED_TABLES:
        desc = TABLE_DESCRIPTIONS.get(table, {})
        table_doc = desc.get("__table__", "(no description)")
        lines.append(f"TABLE {table}")
        lines.append(f"  Purpose: {table_doc}")
        lines.append("  Columns:")
        for col, dtype, nullable in columns_by_table[table]:
            col_doc = desc.get(col, "")
            null_note = " NULLABLE" if nullable else " NOT NULL"
            if col_doc:
                lines.append(f"    - {col} ({dtype}{null_note}): {col_doc}")
            else:
                lines.append(f"    - {col} ({dtype}{null_note})")
        lines.append("")

    lines.append("=== Rules ===")
    lines.append("- Generate a SINGLE SELECT statement. No semicolons except optionally at the end.")
    lines.append("- Only query the tables listed above. Never reference public.*, information_schema, pg_catalog, or any other schema.")
    lines.append("- Use fully schema-qualified names: public_marts.fct_daily_metrics, public_marts.fct_daily_prices.")
    lines.append("- Prefer ORDER BY and LIMIT for result stability. Default LIMIT is 100 if the question doesn't specify.")
    lines.append("- There is NO return_1d column in either table. To compute 1-day return, use this exact window pattern:")
    lines.append("    (close_price_filled - LAG(close_price_filled) OVER (PARTITION BY ticker ORDER BY trade_date))")
    lines.append("    / NULLIF(LAG(close_price_filled) OVER (PARTITION BY ticker ORDER BY trade_date), 0)")
    lines.append("  Wrap it in a CTE or subquery if you need to filter or sort by it.")
    lines.append("- volatility_30d and drawdown are stored as decimals (0.0327 = 3.27%). Do not multiply by 100 in SQL; format in the application layer.")
    lines.append("- trade_date is a DATE; compare with DATE literals like '2015-09-27', not strings.")

    return "\n".join(lines)


if __name__ == "__main__":
    print(build_schema_context())