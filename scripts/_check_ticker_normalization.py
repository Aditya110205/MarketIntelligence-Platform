

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
        norm = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT upper(trim(trailing '.' from ticker)) "
                    "FROM public.silver_sp500_clean ORDER BY 1"
                )
            )
        ]
        print("NORMALIZED:", norm)
        print("COUNT:", len(norm))

        collisions = conn.execute(
            text(
                "SELECT upper(trim(trailing '.' from ticker)) AS t, "
                "       date, count(*) AS n "
                "FROM public.silver_sp500_clean "
                "GROUP BY 1, 2 "
                "HAVING count(*) > 1 "
                "ORDER BY 1, 2"
            )
        ).fetchall()

        print("COLLISIONS:", len(collisions))
        for row in collisions:
            print("  ", tuple(row))

        # Also: for each canonical ticker, how many raw variants feed it?
        print("\nVARIANTS PER CANONICAL TICKER:")
        variants = conn.execute(
            text(
                "SELECT upper(trim(trailing '.' from ticker)) AS canonical, "
                "       ticker AS raw, count(*) AS n "
                "FROM public.silver_sp500_clean "
                "GROUP BY 1, 2 "
                "ORDER BY 1, 2"
            )
        ).fetchall()
        current = None
        for canonical, raw, n in variants:
            if canonical != current:
                print(f"  {canonical}:")
                current = canonical
            print(f"    raw='{raw}'  rows={n}")


if __name__ == "__main__":
    main()