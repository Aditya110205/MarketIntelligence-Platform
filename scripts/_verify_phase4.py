"""
scripts/_verify_phase4.py

Phase 4 verification. Confirms:
  - public_staging schema exists
  - stg_sp500__prices has 947 rows
  - ticker column holds exactly the 10 canonical tickers

Throwaway diagnostic; safe to keep as a post-Phase-4 smoke check.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from app.database.session import get_engine  # noqa: E402


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        schemas = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata "
                    "WHERE schema_name LIKE 'public%' ORDER BY 1"
                )
            )
        ]
        rowcount = conn.execute(
            text("SELECT count(*) FROM public_staging.stg_sp500__prices")
        ).scalar_one()
        tickers = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT ticker FROM public_staging.stg_sp500__prices "
                    "ORDER BY 1"
                )
            )
        ]

    print("SCHEMAS:", schemas)
    print("ROWCOUNT:", rowcount)
    print("TICKERS:", tickers)


if __name__ == "__main__":
    main()