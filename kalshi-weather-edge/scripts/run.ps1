param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing repo-local .venv. Run .\scripts\setup-dev.ps1 from the kalshi-weather-edge directory."
    exit 1
}

Set-Location $RepoRoot
& $VenvPython main.py @CliArgs
exit $LASTEXITCODE
