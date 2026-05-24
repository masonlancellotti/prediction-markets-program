$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }

function Test-NonEmptyArray {
    param($Value)
    return ($null -ne $Value -and @($Value).Count -gt 0)
}

function Test-BroadText {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $true }
    return $Text.ToLowerInvariant() -match "continue improving|work on arb|make better|add features|work on the project|do more"
}

function Assert-TaskShape {
    param(
        [Parameter(Mandatory = $true)]$Task,
        [Parameter(Mandatory = $true)][string]$SourceName,
        [switch]$RequireRunnable
    )

    foreach ($field in @("id","lane","repo_path","status","priority","risk_level","title","objective","allowed_files","forbidden_files","required_tests","success_criteria","stop_conditions")) {
        if (-not $Task.PSObject.Properties.Name.Contains($field)) {
            Add-Failure "$SourceName task is missing required field '$field'."
        }
    }

    if ($Task.PSObject.Properties.Name.Contains("lane") -and $Task.lane -notin @("relative_value","graph","weather","orchestrator")) {
        Add-Failure "$SourceName task '$($Task.id)' has invalid lane '$($Task.lane)'."
    }

    if (Test-BroadText -Text ([string]$Task.objective)) {
        Add-Failure "$SourceName task '$($Task.id)' has a broad/vague objective."
    }

    if ($RequireRunnable) {
        foreach ($field in @("allowed_files","forbidden_files","required_tests","success_criteria","stop_conditions")) {
            if (-not (Test-NonEmptyArray -Value $Task.$field)) {
                Add-Failure "Ready task '$($Task.id)' must have non-empty $field."
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$Task.id) -or [string]::IsNullOrWhiteSpace([string]$Task.title)) {
            Add-Failure "Ready task has empty id or title."
        }
    }
}

$taskQueuePath = Join-Path $Global:StateRoot "TASK_QUEUE.json"
$roadmapPath = Join-Path $Global:StateRoot "ROADMAP_BACKLOG.json"

if (-not (Test-Path -LiteralPath $taskQueuePath -PathType Leaf)) { Add-Failure "Missing TASK_QUEUE.json." }
if (-not (Test-Path -LiteralPath $roadmapPath -PathType Leaf)) { Add-Failure "Missing ROADMAP_BACKLOG.json." }

try { $taskQueue = Get-Content -LiteralPath $taskQueuePath -Raw | ConvertFrom-Json }
catch { Add-Failure "TASK_QUEUE.json is invalid JSON: $($_.Exception.Message)" }
try { $roadmap = Get-Content -LiteralPath $roadmapPath -Raw | ConvertFrom-Json }
catch { Add-Failure "ROADMAP_BACKLOG.json is invalid JSON: $($_.Exception.Message)" }

if ($taskQueue) {
    if (-not $taskQueue.PSObject.Properties.Name.Contains("tasks")) { Add-Failure "TASK_QUEUE.json missing tasks array." }
    foreach ($task in @($taskQueue.tasks)) {
        $isReady = ([string]$task.status) -eq "ready"
        Assert-TaskShape -Task $task -SourceName "TASK_QUEUE" -RequireRunnable:$isReady
    }
}

if ($roadmap) {
    if (-not $roadmap.PSObject.Properties.Name.Contains("items")) { Add-Failure "ROADMAP_BACKLOG.json missing items array." }
    foreach ($item in @($roadmap.items)) {
        foreach ($field in @("id","lane","category","title","why_it_matters","profit_proximity","fake_edge_risk","implementation_effort","review_required","requires_user_help","user_help_needed","prerequisites","allowed_files","forbidden_files","success_criteria","stop_conditions","status","created_from")) {
            if (-not $item.PSObject.Properties.Name.Contains($field)) {
                Add-Failure "ROADMAP_BACKLOG item '$($item.id)' missing $field."
            }
        }
        if (([string]$item.status) -eq "ready") {
            Assert-TaskShape -Task $item -SourceName "ROADMAP_BACKLOG ready item" -RequireRunnable
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host "FAIL: task queue validation failed"
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    exit 1
}

Write-Host "PASS: task queue validation passed"
