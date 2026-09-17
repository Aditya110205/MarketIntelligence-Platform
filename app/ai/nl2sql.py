

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

# --- Import bootstrap so this file works both as `python -m app.ai.nl2sql`
# --- and as a direct `python app/ai/nl2sql.py` invocation.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.ai.schema_context import build_schema_context  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:latest"
TEMPERATURE = 0.0
NUM_PREDICT = 512
REQUEST_TIMEOUT_S = 60

# System prompt. This is intentionally redundant with the === Rules === block
# inside the schema context. The schema context describes *what* is queryable;
# this prompt describes *how to respond*. Together they are the two-layer
# defense against prompt injection in the user question.
SYSTEM_PROMPT = """You are a PostgreSQL query generator for a market-data warehouse.

Your ONLY job is to translate a natural-language question into a single, valid
PostgreSQL SELECT statement that answers it using the provided schema context.

Hard rules — violating any of these is a failure:
1. Output ONLY the SQL. No prose. No explanation. No markdown fences. No
   leading "Here is the query:" text. The first character of your response
   must be the first character of the SQL (typically 'S' from SELECT or 'W'
   from WITH).
2. Exactly ONE statement. A single SELECT. No semicolons except optionally
   one at the very end.
3. SELECT-only. Never emit INSERT, UPDATE, DELETE, DROP, ALTER, CREATE,
   TRUNCATE, GRANT, REVOKE, COPY, or any DDL/DML.
4. Only reference the tables named in the schema context, always fully
   schema-qualified: public_marts.fct_daily_metrics and
   public_marts.fct_daily_prices. Never reference public.*,
   information_schema, pg_catalog, or any other schema.
5. There is NO return_1d column. To compute 1-day return, use the exact
   LAG window pattern shown in the schema context's Rules section. Do not
   invent a column name.
6. Prefer ORDER BY and LIMIT for result stability. Default LIMIT is 100 if
   the question doesn't specify one.
7. volatility_30d and drawdown are decimals (0.0327 = 3.27%). Do not
   multiply by 100. Formatting happens in the application layer, not SQL.
8. trade_date is a DATE. Compare with DATE literals like '2015-09-27',
   never with strings or integers.

If the question cannot be answered from the provided tables, emit a single
SELECT that returns zero rows and a comment explaining why — do NOT invent
tables or columns.

Return only SQL."""


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:sql|SQL)?\s*\n?", re.MULTILINE)
_FENCE_END_RE = re.compile(r"\n?```\s*$", re.MULTILINE)
_LEADING_PROSE_RE = re.compile(
    r"^\s*(?:here(?:'s| is)[^\n]*?:|sure[,!]?[^\n]*?:|the query[^\n]*?:)\s*\n",
    re.IGNORECASE,
)


def _clean_sql(raw: str) -> str:
    """
    Strip presentation artifacts from the model's response.

    This is MECHANICAL normalization only. It does NOT fix bad SQL, does NOT
    rewrite schema names, and does NOT validate. Anything that isn't a
    markdown fence, a leading prose line, or trailing whitespace/semicolons
    is passed through unchanged so that Step 4's validator sees the model's
    real output.
    """
    if raw is None:
        return ""

    text = raw.strip()

    # Strip leading ```sql or ``` fence.
    text = _FENCE_RE.sub("", text, count=1)

    # Strip trailing ``` fence.
    text = _FENCE_END_RE.sub("", text, count=1)

    # Strip a leading prose line like "Here is the query:"
    text = _LEADING_PROSE_RE.sub("", text, count=1)

    # Strip a trailing semicolon or period (Step 4 will re-check the
    # statement terminator if it cares).
    text = text.strip()
    while text.endswith(";") or text.endswith("."):
        text = text[:-1].rstrip()

    return text.strip()


# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

def generate_sql(question: str, schema_context: str) -> str:
    """
    Send a single chat request to Ollama and return the cleaned SQL string.

    Raises RuntimeError on transport failure or a malformed response.
    Does NOT validate the SQL. Does NOT execute it. Does NOT cache.
    """
    if not question or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not schema_context or not schema_context.strip():
        raise ValueError("schema_context must be a non-empty string")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Schema context:\n"
                    f"{schema_context}\n\n"
                    "Question:\n"
                    f"{question}\n\n"
                    "SQL:"
                ),
            },
        ],
        "stream": False,
        "options": {
            "temperature": TEMPERATURE,
            "num_predict": NUM_PREDICT,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlrequest.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            raw_bytes = resp.read()
    except URLError as e:
        raise RuntimeError(
            f"Ollama request failed ({OLLAMA_URL}). Is `ollama serve` running "
            f"and is model '{MODEL}' pulled? Underlying error: {e}"
        ) from e

    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Ollama returned non-JSON response: {raw_bytes[:300]!r}"
        ) from e

    # /api/chat with stream=False returns {"message": {"role": ..., "content": ...}, ...}
    try:
        content = parsed["message"]["content"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(
            f"Ollama response missing message.content. Got keys: "
            f"{list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        ) from e

    return _clean_sql(content)


# ---------------------------------------------------------------------------
# __main__ — three hand-picked test questions
# ---------------------------------------------------------------------------

_TEST_QUESTIONS = [
    # Hard one: forces the LAG window pattern since return_1d doesn't exist.
    "What were the top 5 gainers on 2015-09-27?",
    # Simple aggregate + date filter.
    "What is the average 30-day volatility for AAPL in 2014?",
    # Date arithmetic + specific ticker.
    "Show me MSFT's drawdown on every Friday in September 2015.",
]


def _main() -> int:
    print("Building schema context...")
    t0 = time.perf_counter()
    schema_context = build_schema_context()
    t_schema = time.perf_counter() - t0
    print(f"Schema context built in {t_schema * 1000:.0f} ms "
          f"({len(schema_context)} chars)\n")

    for i, q in enumerate(_TEST_QUESTIONS, start=1):
        print("=" * 78)
        print(f"Q{i}: {q}")
        print("-" * 78)
        t0 = time.perf_counter()
        try:
            sql = generate_sql(q, schema_context)
            elapsed = time.perf_counter() - t0
            print(sql)
            print("-" * 78)
            print(f"elapsed: {elapsed:.2f}s   chars: {len(sql)}")
        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"ERROR after {elapsed:.2f}s: {e}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(_main())