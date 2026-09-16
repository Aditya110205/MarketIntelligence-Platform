

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_REPORTS_DIR = REPO_ROOT / "data" / "run_reports"
VENV_PYTHON_WIN = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PYTHON_NIX = REPO_ROOT / ".venv" / "bin" / "python"


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------
#
# Each stage is a list of argv tokens (no shell=True) run from REPO_ROOT.
# Using argv lists avoids Windows/POSIX quoting differences and is required
# for the PowerShell wrapper around dbt.
#
# NOTE on Bronze: fill in the real script name if it differs. The context
# for Phase 3 lists scripts/run_silver.py, but no Bronze runner was named.
# If Bronze is currently a notebook or manual step, set BRONZE_ENABLED=False
# below and the runner will skip it cleanly.

BRONZE_ENABLED = False
BRONZE_SCRIPT = "scripts/run_bronze.py"  # <-- adjust if the real name differs

STAGES: list[dict] = [
    {
        "name": "bronze",
        "enabled": BRONZE_ENABLED,
        "description": "Ingest raw CSV -> data/bronze/",
        "cmd": ["python", BRONZE_SCRIPT],
        "timeout_sec": 600,
    },
    {
        "name": "silver",
        "enabled": True,
        "description": "PySpark clean -> data/silver/sp500_clean.parquet",
        "cmd": ["python", "scripts/run_silver.py"],
        "timeout_sec": 900,
    },
    {
        "name": "dbt",
        "enabled": True,
        "description": "dbt build (run + test) via scripts/dbt.ps1",
        "cmd": [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "scripts/dbt.ps1",
            "build",
            "--profiles-dir", ".",
        ],
        "timeout_sec": 900,
    },
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    name: str
    description: str
    cmd: list[str]
    enabled: bool
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_sec: Optional[float] = None
    exit_code: Optional[int] = None
    success: Optional[bool] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: Optional[str] = None


@dataclass
class RunReport:
    label: str
    started_at: str
    ended_at: Optional[str] = None
    total_duration_sec: Optional[float] = None
    overall_success: Optional[bool] = None
    python_version: str = ""
    platform: str = ""
    cwd: str = ""
    stages: list[StageResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _tail(text: str, n_lines: int = 40) -> str:
    """Return the last n_lines of text, joined. Keeps reports small."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n_lines:])


def _resolve_python() -> str:
    """
    Prefer the repo venv python so stages use the same interpreter as this
    runner. Fall back to the running interpreter if the venv isn't found.
    """
    if os.name == "nt" and VENV_PYTHON_WIN.exists():
        return str(VENV_PYTHON_WIN)
    if VENV_PYTHON_NIX.exists():
        return str(VENV_PYTHON_NIX)
    return sys.executable


def _run_stage(stage: dict) -> StageResult:
    """
    Run one stage as a subprocess from REPO_ROOT. Never raises; captures
    every failure mode into the returned StageResult.
    """
    result = StageResult(
        name=stage["name"],
        description=stage["description"],
        cmd=stage["cmd"],
        enabled=stage["enabled"],
    )

    if not stage["enabled"]:
        result.success = True
        result.error = "skipped (disabled)"
        return result

    # Substitute 'python' with the resolved venv python for portability.
    cmd = list(stage["cmd"])
    if cmd and cmd[0] == "python":
        cmd[0] = _resolve_python()

    result.cmd = cmd
    result.started_at = _utc_now_iso()
    t0 = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=stage.get("timeout_sec", 900),
        )
        result.exit_code = proc.returncode
        result.stdout_tail = _tail(proc.stdout)
        result.stderr_tail = _tail(proc.stderr)
        result.success = proc.returncode == 0
    except subprocess.TimeoutExpired as e:
        result.success = False
        result.error = f"timeout after {stage.get('timeout_sec')}s"
        result.stdout_tail = _tail((e.stdout or "") if isinstance(e.stdout, str) else "")
        result.stderr_tail = _tail((e.stderr or "") if isinstance(e.stderr, str) else "")
    except FileNotFoundError as e:
        result.success = False
        result.error = f"command not found: {e}"
    except Exception as e:
        result.success = False
        result.error = f"{type(e).__name__}: {e}"
    finally:
        result.ended_at = _utc_now_iso()
        result.duration_sec = round(time.perf_counter() - t0, 3)

    return result


def _write_report(report: RunReport) -> Path:
    RUN_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = report.started_at.replace(":", "").replace("-", "").replace("+00:00", "Z")
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in report.label)
    path = RUN_REPORTS_DIR / f"run_{ts}_{safe_label}.json"
    path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the Market Intelligence pipeline end-to-end.")
    p.add_argument("--label", default="run", help="Tag for the report filename (default: run).")
    p.add_argument("--skip-bronze", action="store_true", help="Skip the Bronze stage.")
    p.add_argument("--skip-silver", action="store_true", help="Skip the Silver stage.")
    p.add_argument("--skip-dbt", action="store_true", help="Skip the dbt stage.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    report = RunReport(
        label=args.label,
        started_at=_utc_now_iso(),
        python_version=sys.version.split()[0],
        platform=sys.platform,
        cwd=str(REPO_ROOT),
    )

    print(f"[run_pipeline] repo       : {REPO_ROOT}")
    print(f"[run_pipeline] python     : {report.python_version} ({report.platform})")
    print(f"[run_pipeline] label      : {report.label}")
    print(f"[run_pipeline] report dir : {RUN_REPORTS_DIR}")
    print()

    t0 = time.perf_counter()

    for stage in STAGES:
        if stage["name"] == "bronze" and args.skip_bronze:
            stage = {**stage, "enabled": False}
        if stage["name"] == "silver" and args.skip_silver:
            stage = {**stage, "enabled": False}
        if stage["name"] == "dbt" and args.skip_dbt:
            stage = {**stage, "enabled": False}

        print(f"[run_pipeline] --> {stage['name']}: {stage['description']}")
        sr = _run_stage(stage)
        report.stages.append(sr)

        status = "OK" if sr.success else "FAIL"
        dur = f"{sr.duration_sec:.3f}s" if sr.duration_sec is not None else "n/a"
        note = f" ({sr.error})" if sr.error else ""
        print(f"[run_pipeline] <-- {stage['name']}: {status} in {dur}{note}")

        if not sr.success:
            # Stop the line. Orchestrators generally do this for linear
            # pipelines: no point running dbt if Silver produced nothing.
            print(f"[run_pipeline] aborting: stage '{stage['name']}' failed.")
            break

    report.ended_at = _utc_now_iso()
    report.total_duration_sec = round(time.perf_counter() - t0, 3)
    report.overall_success = all(s.success for s in report.stages if s.enabled)

    path = _write_report(report)
    print()
    print(f"[run_pipeline] overall   : {'SUCCESS' if report.overall_success else 'FAILURE'}")
    print(f"[run_pipeline] total     : {report.total_duration_sec:.3f}s")
    print(f"[run_pipeline] report    : {path}")

    return 0 if report.overall_success else 1


if __name__ == "__main__":
    raise SystemExit(main())