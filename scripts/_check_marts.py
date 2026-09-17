"""One-shot: does return_1d exist anywhere in public_marts?"""
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text
from app.database.session import get_engine

QUERY = text(
    """
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public_marts'
      AND (column_name LIKE '%return%' OR column_name LIKE '%gain%')
    ORDER BY table_name, column_name
    """
)

with get_engine().connect() as conn:
    rows = conn.execute(QUERY).fetchall()
    if not rows:
        print("(no columns matching return/gain in public_marts)")
    for r in rows:
        print(r.table_name, r.column_name, r.data_type)