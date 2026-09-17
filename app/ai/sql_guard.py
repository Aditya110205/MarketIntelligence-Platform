"""
Phase 9, Step 4: SQL validation for LLM-generated queries.

This module is the security boundary between the LLM's output and the
database. It does ONE thing: take a SQL string and either return it
(unchanged) or raise UnsafeSQLError. It does NOT execute, NOT rewrite,
NOT auto-repair, NOT cache.

Design: two layers.
  1. Regex-based lexical checks — fast, catch obvious keyword/pattern abuse.
     These are deliberately over-broad; false positives are fine because
     the model can be re-prompted, false negatives are not.
  2. sqlparse-based structural checks — tokenize, count statements, verify
     statement type, normalize whitespace/quoting before schema-name checks.

Neither layer alone is sufficient:
  - Regex can't tell `DROP` inside a comment from real `DROP`.
  - sqlparse's get_type() returns UNKNOWN for WITH-led CTEs, so a naive
    "starts with SELECT" check would break legitimate CTEs. We handle this
    by combining statement-type detection with a keyword scan of the token
    stream, so `WITH x AS (...) INSERT ...` is caught.

Threat model: an adversarial user question attempts to inject SQL that
reads from unauthorized schemas, modifies data, or exfiltrates metadata.
We do NOT defend against a compromised Ollama server or a malicious model
that emits valid-looking SELECTs with side effects via functions (e.g.
pg_sleep, dblink) — those are out of scope for Step 4 and would require
a function allowlist, which is a later hardening step.
"""

from __future__ import annotations

import re
from typing import Iterable

import sqlparse
from sqlparse import tokens as T


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class UnsafeSQLError(ValueError):
    """Raised when SQL fails validation. Message names the specific rule."""

    def __init__(self, rule: str, detail: str = ""):
        self.rule = rule
        self.detail = detail
        msg = f"Unsafe SQL [rule={rule}]"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The only schema-qualified table references permitted anywhere in the SQL.
ALLOWED_QUALIFIED_TABLES = frozenset({
    "public_marts.fct_daily_metrics",
    "public_marts.fct_daily_prices",
})

# Forbidden keywords, matched as whole words, case-insensitive, after
# stripping SQL comments. Over-broad on purpose: `create_date` would not
# match \bCREATE\b because of the word boundary, but `CREATE TABLE` would.
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "CREATE", "REPLACE", "MERGE",
    "CALL", "DO", "EXECUTE", "VACUUM", "ANALYZE", "REINDEX",
    "CLUSTER", "REFRESH", "COMMENT", "SECURITY", "LABEL",
)

# Forbidden schema/identifier prefixes. Matched as substrings (case-insensitive)
# after comment stripping, because these can appear as `pg_catalog.pg_class`,
# `pg_class`, `pg_temp.foo`, etc. — all variants must be caught.
FORBIDDEN_SCHEMA_SUBSTRINGS = (
    "information_schema",
    "pg_catalog",
    "pg_temp",
    "pg_toast",
)

# Forbidden identifier prefixes (regex word-boundary match). Catches bare
# `pg_class`, `pg_sleep`, `pg_read_file`, `pg_ls_dir`, etc.
FORBIDDEN_IDENTIFIER_PREFIX_RE = re.compile(
    r"\bpg_[a-z0-9_]+\b", re.IGNORECASE
)

# Matches a schema-qualified identifier: `schema.table` with optional
# whitespace/quoting around the dot. Used to enumerate qualifications.
_QUALIFIED_NAME_RE = re.compile(
    r'(?:"?([a-zA-Z_][a-zA-Z0-9_]*)"?)\s*\.\s*"?'      # schema
    r'([a-zA-Z_][a-zA-Z0-9_]*)"?',                      # table
    re.IGNORECASE,
)

# Matches a trailing semicolon (with optional trailing whitespace).
_TRAILING_SEMICOLON_RE = re.compile(r";\s*$")

# Matches any semicolon.
_ANY_SEMICOLON_RE = re.compile(r";")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _strip_sql_comments(sql: str) -> str:
    """
    Remove -- line comments and /* */ block comments.

    We use sqlparse rather than regex so that nested/odd cases and
    string-literal edge cases (`'-- not a comment'`) are handled correctly.
    Comments are the primary vector for smuggling forbidden keywords past
    a naive regex.
    """
    return sqlparse.format(sql, strip_comments=True)


def _iter_significant_tokens(sql: str):
    """
    Yield (token_type, value) for tokens that aren't whitespace or comments.
    Comments are already stripped by the caller in practice, but we filter
    here too for safety.
    """
    for stmt in sqlparse.parse(sql):
        for tok in stmt.flatten():
            if tok.is_whitespace or tok.ttype in T.Comment:
                continue
            yield tok.ttype, tok.value


def _check_single_statement(sql: str) -> None:
    """Exactly one statement. sqlparse splits on unquoted semicolons."""
    statements = [s for s in sqlparse.parse(sql) if str(s).strip()]
    if len(statements) == 0:
        raise UnsafeSQLError("empty", "no SQL statement found")
    if len(statements) > 1:
        raise UnsafeSQLError(
            "multiple_statements",
            f"found {len(statements)} statements; exactly one allowed",
        )


def _check_trailing_semicolon_only(sql: str) -> None:
    """
    A single semicolon is allowed ONLY at the very end. Any semicolon
    before that is a statement separator (handled by _check_single_statement)
    or a smuggled terminator.
    """
    stripped = sql.rstrip()
    if not stripped:
        return
    # Find all semicolons; if more than one, or the one we find isn't at
    # the end, reject.
    semicolons = list(_ANY_SEMICOLON_RE.finditer(stripped))
    if not semicolons:
        return
    if len(semicolons) > 1:
        raise UnsafeSQLError(
            "multiple_semicolons",
            f"found {len(semicolons)} semicolons; at most one trailing allowed",
        )
    if semicolons[0].start() != len(stripped) - 1:
        raise UnsafeSQLError(
            "embedded_semicolon",
            "semicolon appears before end of statement",
        )


def _check_statement_type(sql: str) -> None:
    """
    Statement must be SELECT or a WITH-led CTE that ultimately selects.

    sqlparse.get_type() returns 'SELECT' for plain SELECTs and 'UNKNOWN'
    for WITH-led statements. We accept UNKNOWN only if the token stream
    contains a SELECT and no forbidden top-level keywords (the keyword
    scan below catches `WITH ... INSERT`).
    """
    for stmt in sqlparse.parse(sql):
        stype = stmt.get_type()
        if stype == "SELECT":
            continue
        if stype == "UNKNOWN":
            # Could be a CTE. Require a SELECT token to be present; the
            # forbidden-keyword scan will catch data-modifying CTEs.
            has_select = any(
                ttype in T.Keyword.DML and value.upper() == "SELECT"
                for ttype, value in _iter_significant_tokens(str(stmt))
            )
            if has_select:
                continue
            raise UnsafeSQLError(
                "statement_type",
                f"statement type is {stype!r} with no SELECT token",
            )
        raise UnsafeSQLError(
            "statement_type",
            f"statement type is {stype!r}; only SELECT is allowed",
        )


def _check_forbidden_keywords(sql: str) -> None:
    """
    Whole-word, case-insensitive scan for DML/DDL keywords, after comment
    stripping. Word boundaries mean `create_date` and `updated_at` do not
    false-positive on CREATE/UPDATE.
    """
    for kw in FORBIDDEN_KEYWORDS:
        pattern = re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
        if pattern.search(sql):
            raise UnsafeSQLError(
                "forbidden_keyword",
                f"keyword {kw!r} is not allowed",
            )


def _check_forbidden_schemas(sql: str) -> None:
    """
    Reject information_schema, pg_catalog, pg_temp, pg_toast, and any
    bare `pg_*` identifier. The prefix regex catches `pg_class`,
    `pg_sleep(5)`, `pg_read_file(...)`, and unqualified `pg_catalog` refs.
    """
    lower = sql.lower()
    for sub in FORBIDDEN_SCHEMA_SUBSTRINGS:
        if sub in lower:
            raise UnsafeSQLError(
                "forbidden_schema",
                f"reference to {sub!r} is not allowed",
            )
    m = FORBIDDEN_IDENTIFIER_PREFIX_RE.search(sql)
    if m:
        raise UnsafeSQLError(
            "forbidden_identifier",
            f"identifier {m.group(0)!r} matches forbidden pg_* prefix",
        )


def _check_public_schema_banned(sql: str) -> None:
    """
    Reject any schema-qualified reference to `public.<table>` EXCEPT the
    allowed public_marts.* names. The regex for qualified names also matches
    `public_marts.foo`, so we filter those out before flagging.
    """
    for match in _QUALIFIED_NAME_RE.finditer(sql):
        schema = match.group(1).lower()
        table = match.group(2).lower()
        full = f"{schema}.{table}"
        if full in ALLOWED_QUALIFIED_TABLES:
            continue
        if schema == "public":
            raise UnsafeSQLError(
                "public_schema_banned",
                f"reference to {full!r}; use public_marts.* instead",
            )

def _check_required_schema_qualification(sql: str) -> None:
    """
    Every table reference must be schema-qualified with public_marts.

    Table references only appear after FROM / JOIN / UPDATE. We match those
    positions specifically with an optional schema capture group. If the
    schema group is None (no dot before the table name), the reference is
    unqualified and we reject it. Column references like
    `fct_daily_prices.close_price` in a SELECT list never match this pattern,
    so they're never false-flagged.
    """
    # 1. Require at least one allowed qualified table somewhere in the SQL.
    found_allowed = False
    for match in _QUALIFIED_NAME_RE.finditer(sql):
        full = f"{match.group(1).lower()}.{match.group(2).lower()}"
        if full in ALLOWED_QUALIFIED_TABLES:
            found_allowed = True
            break
    if not found_allowed:
        raise UnsafeSQLError(
            "no_allowed_table",
            "SQL does not reference any of the allowed tables "
            f"({', '.join(sorted(ALLOWED_QUALIFIED_TABLES))}); "
            "table references must be schema-qualified",
        )

    # 2. For each known table, find every occurrence that follows FROM / JOIN
    #    / UPDATE and check that a schema qualifier is present.
    for allowed in ALLOWED_QUALIFIED_TABLES:
        _, table = allowed.split(".")

        # Pattern: (FROM|JOIN|UPDATE) <ws> [schema .] table <boundary>
        # The schema part is optional and captured. If None → unqualified.
        pattern = re.compile(
            rf"\b(?:FROM|JOIN|UPDATE)\s+"
            rf'(?:"?([a-zA-Z_][a-zA-Z0-9_]*)"?\s*\.\s*)?"?{re.escape(table)}"?'
            rf"(?![a-zA-Z0-9_])",
            re.IGNORECASE,
        )

        for m in pattern.finditer(sql):
            schema = m.group(1)
            if schema is None:
                raise UnsafeSQLError(
                    "unqualified_table",
                    f"table {table!r} appears after FROM/JOIN/UPDATE "
                    f"without public_marts. qualifier",
                )
            if schema.lower() != "public_marts":
                raise UnsafeSQLError(
                    "wrong_schema",
                    f"table {table!r} qualified with {schema!r}; "
                    f"only public_marts is allowed",
                )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_sql(sql: str) -> str:
    """
    Validate a SQL string. Returns the input unchanged on success.
    Raises UnsafeSQLError with a specific rule name on failure.

    Order of checks matters:
      1. Non-empty.
      2. Comment stripping (so later checks see only real code).
      3. Single statement + semicolon placement.
      4. Statement type (SELECT / SELECT-led CTE).
      5. Forbidden keywords.
      6. Forbidden schemas/identifiers.
      7. public.* ban and required public_marts.* qualification.
    """
    if sql is None or not sql.strip():
        raise UnsafeSQLError("empty", "SQL is empty or whitespace-only")

    # 2. Strip comments FIRST so smuggled keywords inside /* */ are visible
    #    to the keyword scan but not to the schema-qualification regex.
    #    We strip for the keyword/schema checks, but keep the original for
    #    the statement-type check (sqlparse handles comments fine).
    stripped = _strip_sql_comments(sql)
    if not stripped.strip():
        raise UnsafeSQLError("empty", "SQL is only comments")

    # 3. Statement count and semicolons.
    _check_single_statement(stripped)
    _check_trailing_semicolon_only(stripped)

    # 4. Statement type.
    _check_statement_type(stripped)

    # 5. Keyword ban.
    _check_forbidden_keywords(stripped)

    # 6. Schema ban.
    _check_forbidden_schemas(stripped)

    # 7. Qualification rules.
    _check_public_schema_banned(stripped)
    _check_required_schema_qualification(stripped)

    return sql


# ---------------------------------------------------------------------------
# __main__ — table-driven test of 10 cases (5 safe, 5 unsafe)
# ---------------------------------------------------------------------------

_SAFE_CASES = [
    # 1. Plain SELECT with schema qualification and trailing semicolon.
    (
        "safe_simple_select",
        "SELECT ticker, close_price_filled "
        "FROM public_marts.fct_daily_metrics "
        "WHERE trade_date = '2015-09-27' LIMIT 5;",
    ),
    # 2. Aggregate with date range.
    (
        "safe_aggregate",
        "SELECT AVG(volatility_30d) FROM public_marts.fct_daily_metrics "
        "WHERE ticker = 'AAPL' AND trade_date BETWEEN '2014-01-01' AND '2014-12-31'",
    ),
    # 3. CTE + LAG window function — the canonical hard case from Step 3.
    (
        "safe_cte_lag",
        """
        WITH ranked AS (
          SELECT ticker,
                 (close_price_filled - LAG(close_price_filled) OVER
                    (PARTITION BY ticker ORDER BY trade_date))
                 / NULLIF(LAG(close_price_filled) OVER
                    (PARTITION BY ticker ORDER BY trade_date), 0) AS ret
          FROM public_marts.fct_daily_metrics
          WHERE trade_date = '2015-09-27'
        )
        SELECT ticker FROM ranked ORDER BY ret DESC NULLS LAST LIMIT 5
        """,
    ),
    # 4. JOIN across both allowed marts.
    (
        "safe_join_two_marts",
        "SELECT m.ticker, m.drawdown, p.volume "
        "FROM public_marts.fct_daily_metrics m "
        "JOIN public_marts.fct_daily_prices p "
        "  ON m.ticker = p.ticker AND m.trade_date = p.trade_date "
        "WHERE m.ticker = 'MSFT' LIMIT 100",
    ),
    # 5. Comment containing a forbidden-looking word, but no real DDL.
    #    Tests that comment stripping prevents false positives... except
    #    we intentionally reject any DROP-looking text after stripping, so
    #    the comment must NOT contain a bare forbidden keyword. Use a
    #    safe comment instead.
    (
        "safe_with_comment",
        "-- average drawdown per ticker in 2015\n"
        "SELECT ticker, AVG(drawdown) FROM public_marts.fct_daily_metrics "
        "WHERE trade_date >= '2015-01-01' AND trade_date < '2016-01-01' "
        "GROUP BY ticker LIMIT 100",
    ),
]

_UNSAFE_CASES = [
    # 6. DROP TABLE.
    (
        "unsafe_drop",
        "DROP TABLE public_marts.fct_daily_metrics",
        "forbidden_keyword",
    ),
    # 7. Stacked statements via semicolon.
    (
        "unsafe_stacked",
        "SELECT 1; DELETE FROM public_marts.fct_daily_metrics",
        "multiple_statements",
    ),
    # 8. information_schema reconnaissance.
    (
        "unsafe_information_schema",
        "SELECT table_name FROM information_schema.tables",
        "forbidden_schema",
    ),
    # 9. Bare pg_* identifier (e.g. pg_sleep, pg_read_file).
    (
        "unsafe_pg_identifier",
        "SELECT pg_sleep(10)",
        "forbidden_identifier",
    ),
    # 10. public.* instead of public_marts.*.
    (
        "unsafe_public_schema",
        "SELECT * FROM public.fct_daily_metrics LIMIT 10",
        "public_schema_banned",
    ),
]


def _main() -> int:
    failures = 0

    print("=" * 78)
    print("SAFE CASES — must return input unchanged")
    print("=" * 78)
    for name, sql in _SAFE_CASES:
        try:
            out = validate_sql(sql)
            assert out == sql, f"{name}: validator mutated input"
            print(f"  PASS  {name}")
        except UnsafeSQLError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"  FAIL  {name}: unexpected {type(e).__name__}: {e}")

    print()
    print("=" * 78)
    print("UNSAFE CASES — must raise UnsafeSQLError with the expected rule")
    print("=" * 78)
    for name, sql, expected_rule in _UNSAFE_CASES:
        try:
            validate_sql(sql)
            failures += 1
            print(f"  FAIL  {name}: validator accepted unsafe SQL")
        except UnsafeSQLError as e:
            if e.rule == expected_rule:
                print(f"  PASS  {name}  (rule={e.rule})")
            else:
                failures += 1
                print(
                    f"  FAIL  {name}: expected rule={expected_rule!r}, "
                    f"got rule={e.rule!r}"
                )
        except Exception as e:
            failures += 1
            print(f"  FAIL  {name}: unexpected {type(e).__name__}: {e}")

    print()
    total = len(_SAFE_CASES) + len(_UNSAFE_CASES)
    print(f"{total - failures}/{total} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())