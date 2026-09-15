"""Phase 3: PySpark Silver layer — clean bronze CSV -> typed Parquet in data/silver/."""
from __future__ import annotations

import os
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

# --- Windows-only: point Spark's Hadoop at winutils.exe (must run before JVM starts) ---
_HADOOP_HOME = os.environ.get("HADOOP_HOME", r"C:\hadoop")
os.environ.setdefault("HADOOP_HOME", _HADOOP_HOME)
os.environ.setdefault("hadoop.home.dir", _HADOOP_HOME)
os.environ["PATH"] = _HADOOP_HOME + os.pathsep + os.environ.get("PATH", "")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRONZE_CSV = REPO_ROOT / "data" / "bronze" / "sp500_daily_stock_prices_2013_2018_unclean.csv"
DEFAULT_SILVER_DIR = REPO_ROOT / "data" / "silver" / "sp500_clean.parquet"


def build_session(app_name: str = "silver_build") -> SparkSession:
    return (
        SparkSession.builder
        .appName(app_name)
        .master("local[*]")
        .config("spark.driver.memory", "2g")
        .getOrCreate()
    )


def clean(df: DataFrame) -> DataFrame:
    """Apply all cleaning rules. Each rule traces to a raw value seen in the diagnostic step."""
    date_parsed = F.coalesce(
        F.to_date(F.col("date"), "yyyy-MM-dd"),
        F.to_date(F.col("date"), "yyyy/MM/dd"),
        F.to_date(F.col("date"), "MM/dd/yyyy"),
        F.to_date(F.col("date"), "MM-dd-yyyy"),
        F.to_date(F.col("date"), "MMM d, yyyy"),
    )

    open_clean = F.regexp_replace(F.col("open"), r"^\$", "").cast("double")
    close_clean = F.regexp_replace(F.col("close"), r"^\$", "").cast("double")
    high_clean = F.col("high").cast("double")
    low_clean = F.col("low").cast("double")

    vol_no_suffix = F.regexp_replace(F.col("volume"), r"(?i)\s*shares\s*$", "")
    vol_no_commas = F.regexp_replace(vol_no_suffix, r",", "")
    volume_clean = vol_no_commas.cast("long")

    ticker_clean = F.trim(F.col("Name"))

    out = df.select(
        date_parsed.alias("date"),
        open_clean.alias("open"),
        high_clean.alias("high"),
        low_clean.alias("low"),
        close_clean.alias("close"),
        volume_clean.alias("volume"),
        ticker_clean.alias("ticker"),
    )

    out = out.filter(F.col("date").isNotNull())
    out = out.filter(F.col("ticker").isNotNull() & (F.col("ticker") != ""))
    out = out.dropDuplicates(["ticker", "date"])
    return out


def run(
    bronze_csv: Path | str = DEFAULT_BRONZE_CSV,
    silver_dir: Path | str = DEFAULT_SILVER_DIR,
    spark: SparkSession | None = None,
) -> dict:
    """Read bronze CSV -> clean -> write Parquet. Returns a small summary dict."""
    owns_session = spark is None
    if owns_session:
        spark = build_session()

    spark.sparkContext.setLogLevel("WARN")
    try:
        raw = (
            spark.read
            .option("header", True)
            .option("inferSchema", False)
            .csv(str(bronze_csv))
        )

        df = clean(raw).cache()

        row_count = df.count()
        null_counts = (
            df.select([F.count(F.when(F.col(c).isNull(), c)).alias(c) for c in df.columns])
            .collect()[0]
            .asDict()
        )
        distinct_tickers = df.select("ticker").distinct().count()
        date_range_row = df.select(
            F.min("date").alias("min_date"), F.max("date").alias("max_date")
        ).collect()[0]

        df.write.mode("overwrite").parquet(str(silver_dir))

        summary = {
            "row_count": row_count,
            "null_counts": null_counts,
            "distinct_tickers": distinct_tickers,
            "min_date": str(date_range_row["min_date"]),
            "max_date": str(date_range_row["max_date"]),
            "silver_dir": str(silver_dir),
        }

        df.unpersist()
        return summary

    finally:
        if owns_session:
            spark.stop()