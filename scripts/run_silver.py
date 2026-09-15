
from __future__ import annotations

import sys
import time
from pathlib import Path

# Make repo root importable so `app.*` works no matter how this script is invoked.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.services.silver import run  # noqa: E402


def main() -> int:
    t0 = time.perf_counter()
    summary = run()
    elapsed = time.perf_counter() - t0

    print("\n=== SILVER SUMMARY ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    print(f"\n=== RUNTIME: {elapsed:.1f} s ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())