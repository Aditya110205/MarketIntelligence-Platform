from __future__ import annotations

import csv
import re
import sys
import time
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


API_BASE = "http://127.0.0.1:8000"
QUESTIONS_CSV = _REPO_ROOT / "docs" / "eval" / "nl2sql_questions.csv"
RESULTS_CSV = _REPO_ROOT / "docs" / "eval" / "nl2sql_results.csv"
REQUEST_TIMEOUT_S = 180


# ---------------------------------------------------------------------------
# SQL feature extraction
# ---------------------------------------------------------------------------

# Tables we know about. Longer names first so `fct_daily_metrics` is matched
# before any prefix overlap with another name.
_KNOWN_TABLES = [
    "public_marts.fct_daily_metrics",
    "public_marts.fct_daily_prices",
]

# Columns we know about. Order matters for the regex alternation: longest
# first, so `close_price_filled` matches before `close_price`.
_KNOWN_COLUMNS = [
    "close_price_was_filled",
    "close_price_filled",
    "close_price",
    "open_price",
    "high_price",
    "low_price",
    "volatility_30d",
    "drawdown",
    "trade_date",
    "volume",
    "ticker",
    "ma7",
    "ma30",
]

# Columns that exist in BOTH marts with identical values, because
# fct_daily_metrics is derived from fct_daily_prices in the dbt pipeline.
# A query that only touches these columns is answered correctly by either
# mart, so tables_match should not penalize picking the "other" one.
_SHARED_COLUMNS = {"ticker", "trade_date", "close_price_filled"}

# Aggregations. Case-insensitive, whole-word.
_AGG_RE = re.compile(
    r"\b(AVG|SUM|MIN|MAX|COUNT|COUNT\s*\(\s*DISTINCT)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_.]*)",
    re.IGNORECASE,
)


def _strip_strings_and_comments(sql: str) -> str:
    """
    Remove string literals and SQL comments so column/table extraction doesn't
    false-positive on words inside quotes (e.g. a ticker named 'AVG').
    """
    # Remove -- line comments
    sql = re.sub(r"--[^\n]*", " ", sql)
    # Remove /* */ block comments
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # Remove single-quoted string literals
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    # Remove double-quoted identifiers
    sql = re.sub(r'"(?:[^"]|"")*"', '""', sql)
    return sql


def extract_features(sql: str) -> dict:
    """
    Parse a SQL string into a set of features for semantic comparison.

    Returns:
        {
          "tables": set of table names (fully qualified),
          "columns": set of column names referenced anywhere,
          "aggregations": set of (agg, column) tuples,
          "filter_columns": set of column names appearing in WHERE clauses,
        }
    """
    clean = _strip_strings_and_comments(sql)
    lower = clean.lower()

    # --- tables ------------------------------------------------------------
    tables = {t for t in _KNOWN_TABLES if t in lower}

    # --- columns -----------------------------------------------------------
    # Match whole-word identifiers against the known-columns list. Handles
    # qualified refs like `m.close_price_filled` because we match the suffix.
    columns = set()
    for col in _KNOWN_COLUMNS:
        # Word-boundary match, but avoid matching inside a longer identifier:
        # `close_price_filled` must not match when looking for `close_price`.
        pattern = re.compile(
            rf"(?<![a-zA-Z0-9_]){re.escape(col)}(?![a-zA-Z0-9_])",
            re.IGNORECASE,
        )
        if pattern.search(clean):
            columns.add(col)

    # --- aggregations ------------------------------------------------------
    aggregations = set()
    for m in _AGG_RE.finditer(clean):
        agg_raw = m.group(1).upper().replace(" ", "")
        col_raw = m.group(2).split(".")[-1].lower()  # strip table qualifier
        # Only record aggregations over known columns. AVG(1) or COUNT(*) are
        # ignored — they're not part of the column set and would confuse match.
        if col_raw in _KNOWN_COLUMNS:
            aggregations.add((agg_raw, col_raw))
        elif col_raw == "*":
            aggregations.add((agg_raw, "*"))

    # --- filter columns ----------------------------------------------------
    # Extract columns appearing in WHERE clauses. Split on WHERE, then take
    # everything up to the next clause keyword (GROUP BY / ORDER BY / LIMIT).
    filter_columns = set()
    for m in re.finditer(
        r"\bWHERE\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        clean,
        re.IGNORECASE | re.DOTALL,
    ):
        where_body = m.group(1)
        for col in _KNOWN_COLUMNS:
            pattern = re.compile(
                rf"(?<![a-zA-Z0-9_]){re.escape(col)}(?![a-zA-Z0-9_])",
                re.IGNORECASE,
            )
            if pattern.search(where_body):
                filter_columns.add(col)

    return {
        "tables": tables,
        "columns": columns,
        "aggregations": aggregations,
        "filter_columns": filter_columns,
    }


def _tables_match(generated: dict, expected: dict) -> bool:
    """
    True if either:
      - both queries reference the exact same set of marts, OR
      - they reference different marts but both queries only touch columns
        shared between the two marts, so the answers are identical.
    """
    if generated["tables"] == expected["tables"]:
        return True
    if generated["columns"].issubset(_SHARED_COLUMNS) and \
       expected["columns"].issubset(_SHARED_COLUMNS):
        return True
    return False


def sqls_match(generated: str, expected: str) -> dict:
    """
    Compare two SQL strings semantically.

    Returns a dict of per-signal booleans plus an overall `match`.
    Overall match requires ALL four signals to agree. That's strict; if it
    turns out to be too strict (correct queries scored wrong because of an
    irrelevant column reference), loosen the overall rule and keep the
    per-signal flags for diagnosis.
    """
    g = extract_features(generated)
    e = extract_features(expected)

    tables_match = _tables_match(g, e)
    columns_match = g["columns"] == e["columns"]
    agg_match = g["aggregations"] == e["aggregations"]
    filter_match = g["filter_columns"] == e["filter_columns"]

    return {
        "tables_match": tables_match,
        "columns_match": columns_match,
        "aggregations_match": agg_match,
        "filters_match": filter_match,
        "match": tables_match and columns_match and agg_match and filter_match,
        "generated_features": g,
        "expected_features": e,
    }


# ---------------------------------------------------------------------------
# Eval driver
# ---------------------------------------------------------------------------

def _load_questions() -> list[dict]:
    if not QUESTIONS_CSV.exists():
        raise SystemExit(f"Missing {QUESTIONS_CSV}")
    with QUESTIONS_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"{QUESTIONS_CSV} is empty")
    required = {"id", "question", "expected_sql"}
    missing = required - set(rows[0].keys())
    if missing:
        raise SystemExit(f"{QUESTIONS_CSV} missing columns: {missing}")
    return rows


def _check_api() -> None:
    try:
        r = requests.get(f"{API_BASE}/health", timeout=5)
    except requests.RequestException as e:
        raise SystemExit(
            f"API not reachable at {API_BASE}: {e}\n"
            f"Start it with: uvicorn app.api.main:app --host 127.0.0.1 --port 8000"
        )
    if r.status_code != 200:
        raise SystemExit(f"/health returned HTTP {r.status_code}: {r.text}")


def main() -> int:
    _check_api()
    questions = _load_questions()

    print(f"Running {len(questions)} questions through {API_BASE}/ask")
    print(f"Reference SQL: {QUESTIONS_CSV}")
    print(f"Writing per-question detail to: {RESULTS_CSV}")
    print()

    correct = 0
    signal_counts = {
        "tables_match": 0,
        "columns_match": 0,
        "aggregations_match": 0,
        "filters_match": 0,
    }
    detail_rows: list[dict] = []
    wall_start = time.perf_counter()

    for i, row in enumerate(questions, start=1):
        qid = row["id"]
        question = row["question"]
        expected = row["expected_sql"]

        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{API_BASE}/ask",
                json={"question": question},
                timeout=REQUEST_TIMEOUT_S,
            )
        except requests.RequestException as e:
            print(f"[{i:02d}/{len(questions)}] id={qid} REQUEST FAILED: {e}")
            detail_rows.append({
                "id": qid, "question": question, "expected_sql": expected,
                "generated_sql": "", "status": "request_failed",
                "error": str(e), "match": False,
            })
            continue
        elapsed = time.perf_counter() - t0

        if r.status_code != 200:
            # /ask returns non-200 for unsafe SQL or execution failure.
            # Record the failure and treat as incorrect.
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            print(f"[{i:02d}/{len(questions)}] id={qid} HTTP {r.status_code}  ({elapsed:.1f}s)")
            detail_rows.append({
                "id": qid, "question": question, "expected_sql": expected,
                "generated_sql": "", "status": f"http_{r.status_code}",
                "error": str(detail)[:300], "match": False,
            })
            continue

        body = r.json()
        generated = body.get("sql", "")
        cmp = sqls_match(generated, expected)

        if cmp["match"]:
            correct += 1
            mark = "PASS"
        else:
            mark = "FAIL"
        for k in signal_counts:
            if cmp[k]:
                signal_counts[k] += 1

        print(
            f"[{i:02d}/{len(questions)}] id={qid} {mark}  ({elapsed:.1f}s)  "
            f"tables={'Y' if cmp['tables_match'] else 'N'} "
            f"cols={'Y' if cmp['columns_match'] else 'N'} "
            f"agg={'Y' if cmp['aggregations_match'] else 'N'} "
            f"filters={'Y' if cmp['filters_match'] else 'N'}"
        )

        detail_rows.append({
            "id": qid,
            "question": question,
            "expected_sql": expected,
            "generated_sql": generated,
            "status": "ok",
            "error": "",
            "tables_match": cmp["tables_match"],
            "columns_match": cmp["columns_match"],
            "aggregations_match": cmp["aggregations_match"],
            "filters_match": cmp["filters_match"],
            "match": cmp["match"],
            "latency_s": round(elapsed, 2),
        })

    wall_elapsed = time.perf_counter() - wall_start
    total = len(questions)
    pct = (correct / total * 100) if total else 0.0

    print()
    print("=" * 70)
    print(f"RESULT: {correct}/{total} correct = {pct:.1f}%")
    print("=" * 70)
    print(f"  tables_match:       {signal_counts['tables_match']}/{total}")
    print(f"  columns_match:      {signal_counts['columns_match']}/{total}")
    print(f"  aggregations_match: {signal_counts['aggregations_match']}/{total}")
    print(f"  filters_match:      {signal_counts['filters_match']}/{total}")
    print(f"  wall time:          {wall_elapsed:.1f}s "
          f"({wall_elapsed / total:.1f}s per question)")
    print()

    # Write detail CSV for inspection. Sort by id numerically.
    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id", "question", "expected_sql", "generated_sql", "status", "error",
        "tables_match", "columns_match", "aggregations_match", "filters_match",
        "match", "latency_s",
    ]
    with RESULTS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for d in detail_rows:
            w.writerow(d)

    print(f"Per-question detail written to {RESULTS_CSV}")
    return 0 if correct == total else 1


if __name__ == "__main__":
    raise SystemExit(main())