import csv
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.eval_nl2sql import extract_features

rows = {r["id"]: r for r in csv.DictReader((_REPO_ROOT / "docs/eval/nl2sql_results.csv").open(encoding="utf-8"))}

for qid in ["2", "16", "6", "17", "19", "20", "28"]:
    r = rows.get(qid)
    if not r:
        continue
    print(f"=== id={qid} ===")
    print(f"expected_sql: {r['expected_sql']}")
    print(f"generated_sql: {r['generated_sql']}")
    print(f"expected features: {extract_features(r['expected_sql'])}")
    print(f"generated features: {extract_features(r['generated_sql'])}")
    print()