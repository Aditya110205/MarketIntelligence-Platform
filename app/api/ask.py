"""
app/api/ask.py

POST /ask endpoint. Wires Phase 9 Steps 2-5 together:

    app.ai.schema_context.build_schema_context   (Step 2, cached)
    app.ai.nl2sql.generate_sql                   (Step 3)
    app.ai.sql_guard.validate_sql                (Step 4)
    app.ai.explain.generate_explanation          (Step 5)

Flow: question -> schema context -> SQL -> validate -> execute -> explain.

On validation failure, returns HTTP 400 with the rule name so the caller
can see WHY the SQL was rejected. Does NOT retry — Step 6 will measure
first-pass accuracy, and a retry loop is a later hardening step.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.ai.schema_context import build_schema_context  # noqa: E402
from app.ai.nl2sql import generate_sql  # noqa: E402
from app.ai.sql_guard import validate_sql, UnsafeSQLError  # noqa: E402
from app.ai.explain import generate_explanation  # noqa: E402
from app.database.session import get_engine  # noqa: E402


router = APIRouter(tags=["ai"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class AskResponse(BaseModel):
    question: str
    sql: str
    rows: list[dict[str, Any]]
    row_count: int
    explanation: str
    latency_ms: dict[str, float]


def _execute_sql(sql: str) -> list[dict[str, Any]]:
    """Run the validated SQL and return rows as list-of-dict."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    question = req.question.strip()
    latency: dict[str, float] = {}

    # --- 1. Generate SQL ---------------------------------------------------
    schema_context = build_schema_context()
    t0 = time.perf_counter()
    try:
        raw_sql = generate_sql(question, schema_context)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NL->SQL failed: {e}")
    latency["sql_gen_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # --- 2. Validate -------------------------------------------------------
    t0 = time.perf_counter()
    try:
        safe_sql = validate_sql(raw_sql)
    except UnsafeSQLError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsafe_sql",
                "rule": e.rule,
                "message": str(e),
                "generated_sql": raw_sql,
            },
        )
    latency["validate_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # --- 3. Execute --------------------------------------------------------
    t0 = time.perf_counter()
    try:
        rows = _execute_sql(safe_sql)
    except Exception as e:
        # The DB rejected the SQL — the model generated something that
        # validates but doesn't run. Surface it, don't hide it.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "sql_execution_failed",
                "message": str(e),
                "generated_sql": safe_sql,
            },
        )
    latency["execute_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # --- 4. Explain --------------------------------------------------------
    t0 = time.perf_counter()
    try:
        explanation = generate_explanation(question, safe_sql, rows)
    except Exception as e:
        # Explanation failure is non-fatal — return the data with a stub.
        explanation = f"(Explanation unavailable: {e})"
    latency["explain_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    latency["total_ms"] = round(sum(latency.values()), 1)

    return AskResponse(
        question=question,
        sql=safe_sql,
        rows=rows,
        row_count=len(rows),
        explanation=explanation,
        latency_ms=latency,
    )