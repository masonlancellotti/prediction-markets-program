param()

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $RepoRoot "requirements.txt"

Set-Location $RepoRoot

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating repo-local virtual environment at $VenvDir"
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        & py -m venv $VenvDir
    } else {
        & python -m venv $VenvDir
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Error "Failed to create .venv. Install Python, then rerun .\scripts\setup-dev.ps1"
    exit 1
}

if (-not (Test-Path $Requirements)) {
    Write-Error "requirements.txt not found at $Requirements"
    exit 1
}

Write-Host "Upgrading pip inside repo-local .venv"
& $VenvPython -m pip install --upgrade pip

Write-Host "Installing repo dependencies from requirements.txt into .venv"
& $VenvPython -m pip install -r $Requirements

Write-Host "Running environment doctor with repo-local .venv"
& $VenvPython scripts\env_doctor.py
exit $LASTEXITCODE
