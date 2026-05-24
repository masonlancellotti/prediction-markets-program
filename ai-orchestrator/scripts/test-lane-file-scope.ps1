param(
    [Parameter(Mandatory = $true)][string]$LaneName,
    [string]$TaskId = "",
    [switch]$AllowAiLoopRuntime
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }

function Convert-TaskPatternToRepoPattern {
    param(
        [Parameter(Mandatory = $true)][string]$Pattern,
        [Parameter(Mandatory = $true)][string]$RepoPath
    )
    $p = ($Pattern -replace "\\", "/").TrimStart("/")
    $repo = ($RepoPath -replace "\\", "/").Trim("/")
    if ($p.StartsWith("$repo/") -or $p -eq $repo) { return $p }
    return "$repo/$p"
}

function Test-PathPattern {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Pattern
    )
    $wild = ($Pattern -replace "\\", "/") -replace "\*\*", "*"
    return (($Path -replace "\\", "/") -like $wild)
}

$lane = Get-AiLane -LaneName $LaneName
$taskQueue = Get-Content -LiteralPath (Join-Path $Global:StateRoot "TASK_QUEUE.json") -Raw | ConvertFrom-Json
$task = $null
if (-not [string]::IsNullOrWhiteSpace($TaskId)) {
    $task = @($taskQueue.tasks | Where-Object { $_.id -eq $TaskId }) | Select-Object -First 1
}
else {
    $task = @($taskQueue.tasks | Where-Object { $_.lane -eq $LaneName -and $_.status -eq "ready" } | Sort-Object priority) | Select-Object -First 1
}
if (-not $task) { throw "No task found for lane '$LaneName'." }

$repoPath = [string]$task.repo_path
$allowed = @($task.allowed_files | ForEach-Object { Convert-TaskPatternToRepoPattern -Pattern ([string]$_) -RepoPath $repoPath })
$forbidden = @($task.forbidden_files | ForEach-Object { Convert-TaskPatternToRepoPattern -Pattern ([string]$_) -RepoPath $repoPath })
$dangerPatterns = @(
    ".env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.sqlite",
    "*.db",
    "*/node_modules/*",
    "ai-orchestrator/logs/*",
    "*private*key*",
    "*secret*"
)

Push-Location $Global:RepoRoot
try {
    $changed = @(& git diff --name-only 2>$null)
    $changed += @(& git ls-files --others --exclude-standard 2>$null)
}
finally {
    Pop-Location
}

$changed = @($changed | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
foreach ($file in $changed) {
    $path = ($file -replace "\\", "/")
    foreach ($danger in $dangerPatterns) {
        if (Test-PathPattern -Path $path -Pattern $danger) {
            Add-Failure "Dangerous changed/untracked path: $path"
        }
    }
    foreach ($pattern in $forbidden) {
        if (Test-PathPattern -Path $path -Pattern $pattern) {
            Add-Failure "Changed path matches forbidden scope: $path via $pattern"
        }
    }
    $inAiLoop = $path -like "$repoPath/.ai_loop/*"
    if ($inAiLoop -and $AllowAiLoopRuntime) { continue }
    $isAllowed = $false
    foreach ($pattern in $allowed) {
        if (Test-PathPattern -Path $path -Pattern $pattern) {
            $isAllowed = $true
            break
        }
    }
    if (-not $isAllowed) {
        Add-Failure "Changed path outside allowed scope for task '$($task.id)': $path"
    }
}

if ($failures.Count -gt 0) {
    Write-Host "FAIL: file scope validation failed for lane '$LaneName' task '$($task.id)'"
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    exit 1
}

Write-Host "PASS: file scope validation passed for lane '$LaneName' task '$($task.id)'"
