@echo off
REM ===========================================================================
REM scripts/run_pipeline.bat
REM
REM Entry point for scheduled / manual pipeline runs on Windows.
REM
REM What it does:
REM   1. cd to repo root (Task Scheduler launches with cwd=C:\Windows\System32)
REM   2. Verify venv exists
REM   3. Create data\logs\ if missing
REM   4. Run scripts\run_pipeline.py with a timestamped label
REM   5. Tee all output to data\logs\pipeline_<timestamp>.log
REM   6. Propagate the pipeline's exit code to Task Scheduler
REM
REM Usage:
REM   scripts\run_pipeline.bat
REM   scripts\run_pipeline.bat manual-retry
REM
REM Task Scheduler invocation (Action > Start a program):
REM   Program:  C:\Users\Aditya\OneDrive\Desktop\Practice\MarketIntelligence\scripts\run_pipeline.bat
REM   Start in: C:\Users\Aditya\OneDrive\Desktop\Practice\MarketIntelligence
REM
REM See docs/decisions/ADR-002-orchestration.md for why this exists instead
REM of an Airflow DAG.
REM ===========================================================================

setlocal enableextensions enabledelayedexpansion

REM --- Resolve repo root from this script's location -------------------------
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

REM --- Timestamp: YYYYMMDD_HHMM ---------------------------------------------
for /f "tokens=1-4 delims=/-. " %%a in ("%DATE%") do (
    set "DD=%%a" & set "MM=%%b" & set "YYYY=%%c"
)
REM %TIME% is HH:MM:SS.cc - take just HHMM
set "HHMM=%TIME:~0,2%%TIME:~3,2%"
set "HHMM=%HHMM: =0%"
set "STAMP=%YYYY%%MM%%DD%_%HHMM%"

REM --- Label: first arg, default "scheduled" --------------------------------
set "LABEL=%~1"
if "%LABEL%"=="" set "LABEL=scheduled"

REM --- Sanity: venv must exist ----------------------------------------------
if not exist "%REPO_ROOT%\venv\Scripts\python.exe" (
    echo [run_pipeline.bat] ERROR: venv not found at %REPO_ROOT%\venv
    echo [run_pipeline.bat] Create it with: python -m venv venv
    exit /b 2
)

REM --- Log dir ---------------------------------------------------------------
if not exist "%REPO_ROOT%\data\logs" mkdir "%REPO_ROOT%\data\logs"
set "LOG_FILE=%REPO_ROOT%\data\logs\pipeline_%STAMP%_%LABEL%.log"

echo [run_pipeline.bat] repo : %REPO_ROOT%
echo [run_pipeline.bat] log  : %LOG_FILE%
echo [run_pipeline.bat] label: %LABEL%
echo.

REM --- Run, tee to console + log file ---------------------------------------
REM Using PowerShell for Tee-Object. -Command string is quoted for cmd.exe.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '%REPO_ROOT%\venv\Scripts\python.exe' '%REPO_ROOT%\scripts\run_pipeline.py' --label '%LABEL%_%STAMP%' 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"

set "RC=%ERRORLEVEL%"

echo.
echo [run_pipeline.bat] exit code: %RC%
echo [run_pipeline.bat] log file : %LOG_FILE%

endlocal & exit /b %RC%