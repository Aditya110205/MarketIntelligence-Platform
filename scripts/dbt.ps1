# scripts/dbt.ps1
# Wrapper for dbt invocations: sets cwd, loads .env into the process env,
# then forwards all arguments to dbt.
#
# Usage (from anywhere in the repo):
#   .\scripts\dbt.ps1 run  --select int_sp500__prices_enriched+ --profiles-dir .
#   .\scripts\dbt.ps1 test --select int_sp500__prices_enriched  --profiles-dir .

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $DbtArgs
)

$ErrorActionPreference = 'Stop'

# Resolve repo root from this script's own path, not the caller's cwd.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$DbtDir   = Join-Path $RepoRoot 'dbt'
$EnvFile  = Join-Path $RepoRoot '.env'

if (-not (Test-Path $EnvFile)) {
    Write-Error ".env not found at $EnvFile"
    exit 1
}

# Load .env into the current process's environment. Same regex as the manual
# one-liner, so behavior stays consistent.
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.*)\s*$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

# cd into dbt/ so `--profiles-dir .` and dbt_project.yml resolve as expected.
Push-Location $DbtDir
try {
    & dbt @DbtArgs
    $code = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $code