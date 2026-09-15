"""
scripts/load_silver_to_postgres.py

Idempotent loader: data/silver/sp500_clean.parquet/  ->  public.silver_sp500_clean

Re-runnable any number of times. Drop-and-recreate inside a single transaction.
Reuses Phase 1 DatabaseConfig (reads .env via python-dotenv).

Usage:
    python scripts/load_silver_to_postgres.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
from sqlalchemy import text

# --- repo-root import shim so `python scripts/...` works without install ---
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.database.session import get_engine  # noqa: E402
from app.utils.config import get_database_config  # noqa: E402

SILVER_PARQUET_DIR = REPO_ROOT / "data" / "silver" / "sp500_clean.parquet"
TARGET_TABLE = "silver_sp500_clean"
TARGET_SCHEMA = "public"


def _read_silver_parquet(parquet_dir: Path) -> pd.DataFrame:
    if not parquet_dir.exists():
        raise FileNotFoundError(f"Silver parquet dir not found: {parquet_dir}")
    if not parquet_dir.is_dir():
        raise NotADirectoryError(
            f"Expected a Spark-written directory, got a file: {parquet_dir}"
        )
    # pandas+pyarrow reads the directory (part-*.snappy.parquet), ignores _SUCCESS
    df = pd.read_parquet(parquet_dir, engine="pyarrow")
    if df.empty:
        raise ValueError(f"Silver parquet at {parquet_dir} is empty.")
    return df


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lock the dtype contract *before* it hits Postgres.
    Silver schema from Phase 3:
        date    date
        open    double
        high    double
        low     double
        close   double
        volume  long
        ticker  string
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")
    df["ticker"] = df["ticker"].astype("string")
    return df


def load() -> dict:
    t0 = time.perf_counter()

    cfg = get_database_config()
    engine = get_engine()

    df = _read_silver_parquet(SILVER_PARQUET_DIR)
    df = _coerce_types(df)

    # One transaction: drop + create + insert. Roll back everything on failure.
    with engine.begin() as conn:
        conn.execute(
            text(f'DROP TABLE IF EXISTS {TARGET_SCHEMA}.{TARGET_TABLE} CASCADE;')
        )
        # Let pandas create the table via to_sql inside this same transaction.
        df.to_sql(
            name=TARGET_TABLE,
            con=conn,
            schema=TARGET_SCHEMA,
            if_exists="append",  # we already dropped; append == create + insert
            index=False,
            method="multi",
            chunksize=500,
        )

    elapsed = time.perf_counter() - t0

    # Verify by reading back the count from the DB (not trusting in-memory df)
    with engine.connect() as conn:
        db_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {TARGET_SCHEMA}.{TARGET_TABLE};")
        ).scalar_one()

    summary = {
        "target": f"{cfg.db}.{TARGET_SCHEMA}.{TARGET_TABLE}",
        "source_dir": str(SILVER_PARQUET_DIR),
        "rows_in_memory": int(len(df)),
        "rows_in_db": int(db_count),
        "columns": list(df.columns),
        "elapsed_sec": round(elapsed, 3),
    }
    return summary


def main() -> None:
    summary = load()
    print("=" * 60)
    print("Silver -> Postgres load complete")
    print("=" * 60)
    print(f"  target       : {summary['target']}")
    print(f"  source_dir   : {summary['source_dir']}")
    print(f"  rows_in_mem  : {summary['rows_in_memory']}")
    print(f"  rows_in_db   : {summary['rows_in_db']}")
    print(f"  columns      : {summary['columns']}")
    print(f"  elapsed_sec  : {summary['elapsed_sec']}")
    print("=" * 60)

    if summary["rows_in_memory"] != summary["rows_in_db"]:
        raise SystemExit(
            f"ROW COUNT MISMATCH: memory={summary['rows_in_memory']} "
            f"db={summary['rows_in_db']}"
        )


if __name__ == "__main__":
    main()