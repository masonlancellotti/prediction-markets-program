$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
$codexReady = New-Object System.Collections.Generic.List[string]

function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }
function Add-Warning { param([string]$Message) $warnings.Add($Message) | Out-Null }

function Invoke-Check {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Command
    )
    Write-Host "Running $Name..."
    $exe = $Command[0]
    $args = @($Command | Select-Object -Skip 1)
    & $exe @args
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "$Name failed with exit code $LASTEXITCODE."
    }
}

Write-Host "AI autonomy preflight"
Write-Host "Repo root: $Global:RepoRoot"

$modePath = Join-Path $Global:StateRoot "AUTONOMY_MODE.json"
if (-not (Test-Path -LiteralPath $modePath -PathType Leaf)) {
    Add-Failure "Missing AUTONOMY_MODE.json."
    $mode = "unknown"
}
else {
    try {
        $autonomy = Get-Content -LiteralPath $modePath -Raw | ConvertFrom-Json
        $mode = [string]$autonomy.mode
        if ($mode -notin @("off","gpt_one_shot_only","codex_one_task","one_lane_supervised","multi_lane_supervised")) {
            Add-Failure "Unsupported autonomy mode: $mode"
        }
    }
    catch {
        Add-Failure "AUTONOMY_MODE.json is invalid: $($_.Exception.Message)"
        $mode = "unknown"
    }
}
Write-Host "Autonomy mode: $mode"

Invoke-Check -Name "orchestrator setup" -Command @("powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "test-orchestrator-setup.ps1"))
Invoke-Check -Name "task queue" -Command @("powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "test-task-queue.ps1"))
Invoke-Check -Name "review gating" -Command @("powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $PSScriptRoot "test-review-gating.ps1"))

foreach ($lane in Get-ExistingAiLanes) {
    $promptPath = Get-AiLoopFile -Lane $lane -FileName "NEXT_CODEX_PROMPT.md"
    $prompt = Read-TextIfExists -Path $promptPath
    if ([string]::IsNullOrWhiteSpace($prompt) -or $prompt.Trim().Equals("No active prompt assigned.", [System.StringComparison]::OrdinalIgnoreCase)) {
        Add-Warning "$($lane.Name): no active Codex prompt assigned; GPT one-shot can prepare one."
        continue
    }

    Write-Host "Running prompt quality for $($lane.Name)..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-next-codex-prompt.ps1") -LaneName $lane.Name
    if ($LASTEXITCODE -ne 0) {
        Add-Failure "prompt quality failed for lane $($lane.Name)."
    }
    else {
        $codexReady.Add($lane.Name) | Out-Null
    }
}

Write-Host "Running git danger scan..."
Push-Location $Global:RepoRoot
try {
    $status = @(& git status --short 2>$null)
}
finally {
    Pop-Location
}
foreach ($line in $status) {
    if ($line -match "(?i)(\.env|\.pem|\.key|secret|node_modules|\.db|\.sqlite)") {
        Add-Failure "Dangerous file appears in git status: $line"
    }
}

foreach ($warning in $warnings) { Write-Host "WARN: $warning" }

if ($failures.Count -gt 0) {
    Write-Host "BLOCKED"
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    exit 1
}

Write-Host "READY_FOR_GPT_ONE_SHOT"
if ($codexReady.Count -gt 0 -and $mode -in @("codex_one_task","one_lane_supervised","multi_lane_supervised")) {
    Write-Host "READY_FOR_CODEX_ONE_TASK: $($codexReady -join ', ')"
}
elseif ($codexReady.Count -gt 0) {
    Write-Host "READY_FOR_CODEX_ONE_TASK requires AUTONOMY_MODE mode codex_one_task or higher. Prompt-ready lanes: $($codexReady -join ', ')"
}
else {
    Write-Host "READY_FOR_CODEX_ONE_TASK: no active passing prompt yet; run GPT one-shot first."
}
