
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma3:latest"
TEMPERATURE = 0.0
NUM_PREDICT = 256
REQUEST_TIMEOUT_S = 60
MAX_ROWS_IN_PROMPT = 5


SYSTEM_PROMPT = """You are a data analyst explaining query results to a
non-technical reader.

Rules:
1. Write ONE short paragraph (3-5 sentences). No bullet points, no headers,
   no markdown formatting.
2. Lead with the direct answer to the question. Then add one sentence of
   context (what the numbers mean, or how they compare).
3. Use plain English. Avoid SQL jargon, column names, and schema names —
   translate them. Say "closing price," not "close_price_filled."
4. If the result set is empty, say so plainly and suggest one plausible
   reason (wrong date, ticker not tracked, etc.). Do NOT invent numbers.
5. Do NOT mention the SQL, the query, the database, or that you were given
   rows. Speak as if you looked at the data and are reporting what you saw.
6. Do NOT use phrases like "Based on the data provided" or "According to the
   query results." Just state the finding.

Return only the paragraph."""


def _format_rows(rows: list[dict], max_rows: int = MAX_ROWS_IN_PROMPT) -> str:
    """Render up to max_rows as a compact table for the prompt."""
    if not rows:
        return "(no rows returned)"
    head = rows[:max_rows]
    cols = list(head[0].keys())
    lines = [" | ".join(cols)]
    lines.append("-" * len(lines[0]))
    for r in head:
        lines.append(" | ".join(str(r.get(c, "")) for c in cols))
    if len(rows) > max_rows:
        lines.append(f"... ({len(rows) - max_rows} more rows omitted)")
    return "\n".join(lines)


def generate_explanation(
    question: str,
    sql: str,
    rows: list[dict],
    max_rows: int = MAX_ROWS_IN_PROMPT,
) -> str:
    """
    One Ollama call. Returns a single-paragraph explanation string.

    Raises RuntimeError on transport failure or malformed response.
    """
    if not question or not question.strip():
        raise ValueError("question must be non-empty")
    if sql is None or not sql.strip():
        raise ValueError("sql must be non-empty")
    if rows is None:
        rows = []

    row_block = _format_rows(rows, max_rows=max_rows)

    user_content = (
        f"Question: {question}\n\n"
        f"Result rows ({len(rows)} total, showing up to {max_rows}):\n"
        f"{row_block}\n\n"
        "Write the paragraph."
    )

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
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
            f"Ollama explanation request failed ({OLLAMA_URL}): {e}"
        ) from e

    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Ollama returned non-JSON: {raw_bytes[:300]!r}"
        ) from e

    try:
        content = parsed["message"]["content"]
    except (KeyError, TypeError) as e:
        raise RuntimeError(
            f"Ollama response missing message.content: keys="
            f"{list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        ) from e

    return content.strip()


if __name__ == "__main__":
    demo_rows = [
        {"ticker": "AAPL", "volatility_30d": 0.0182},
        {"ticker": "MSFT", "volatility_30d": 0.0164},
        {"ticker": "NVDA", "volatility_30d": 0.0241},
    ]
    out = generate_explanation(
        "Which tickers were most volatile in 2014?",
        "SELECT ticker, AVG(volatility_30d) AS volatility_30d "
        "FROM public_marts.fct_daily_metrics "
        "WHERE trade_date >= '2014-01-01' AND trade_date < '2015-01-01' "
        "GROUP BY ticker ORDER BY volatility_30d DESC LIMIT 3",
        demo_rows,
    )
    print(out)