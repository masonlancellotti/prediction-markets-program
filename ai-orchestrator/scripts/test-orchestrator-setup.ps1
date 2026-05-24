$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }
function Add-SoftWarning { param([string]$Message) $warnings.Add($Message) | Out-Null }

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Add-Failure "Missing file: $Path"
    }
}

function Assert-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-File -Path $Path
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        try { $null = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json }
        catch { Add-Failure "Invalid JSON: $Path - $($_.Exception.Message)" }
    }
}

function Get-NodeForCheck {
    $bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $bundled -PathType Leaf) { return $bundled }
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) { return $node.Source }
    return ""
}

Write-Host "AI orchestrator setup check"
Write-Host "Repo root: $Global:RepoRoot"
Write-Host "Package path: $Global:NodeRoot"
Write-Host ""

$contextFiles = @(
    "PROJECT_CHARTER.md",
    "GLOBAL_GUARDRAILS.md",
    "COMMAND_POLICY.md",
    "PROMPTER_POLICY.md",
    "OUTPUT_SCHEMAS.md",
    "MODEL_ROUTING_POLICY.md"
)

$stateMarkdownFiles = @(
    "PROGRAM_STATUS.md",
    "ACTIVE_GOALS.md",
    "NEXT_STEPS.md",
    "DECISION_LOG.md",
    "FEATURE_IDEAS.md",
    "USER_ACTION_REQUIRED.md",
    "BLOCKER_ANALYSIS.md",
    "VENUE_EXPANSION_PLAN.md"
)

$stateJsonFiles = @(
    "TASK_QUEUE.json",
    "REVIEW_POLICY.json",
    "ROADMAP_BACKLOG.json"
)

Write-Host "Checking context and state files..."
foreach ($file in $contextFiles) { Assert-File -Path (Join-Path $Global:ContextRoot $file) }
foreach ($file in $stateMarkdownFiles) { Assert-File -Path (Join-Path $Global:StateRoot $file) }
foreach ($file in $stateJsonFiles) { Assert-JsonFile -Path (Join-Path $Global:StateRoot $file) }

Write-Host "Checking lanes and .ai_loop files..."
foreach ($name in $Global:Lanes.Keys) {
    $lane = $Global:Lanes[$name]
    if (-not (Test-Path -LiteralPath $lane.Path -PathType Container)) {
        Add-Failure "Lane folder missing: $name -> $($lane.Path)"
        continue
    }
    foreach ($file in $Global:AiLoopFiles) {
        Assert-File -Path (Join-Path (Join-Path $lane.Path ".ai_loop") $file)
    }
}

Write-Host "Checking PowerShell parse..."
Get-ChildItem -LiteralPath $Global:OrchestratorRoot -Recurse -File -Filter *.ps1 |
    Where-Object { $_.FullName -notmatch "\\node_modules\\" } |
    ForEach-Object {
        $parseErrors = $null
        [System.Management.Automation.PSParser]::Tokenize((Get-Content -LiteralPath $_.FullName -Raw), [ref]$parseErrors) | Out-Null
        if ($parseErrors) {
            Add-Failure "PowerShell parse failed: $($_.FullName) - $(($parseErrors | ForEach-Object { $_.Message }) -join '; ')"
        }
    }

Write-Host "Checking Node package and ESM scripts..."
$packagePath = Join-Path $Global:NodeRoot "package.json"
Assert-JsonFile -Path $packagePath
if (Test-Path -LiteralPath $packagePath -PathType Leaf) {
    $pkg = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
    if ($pkg.type -ne "module") { Add-Failure "ai-orchestrator/node/package.json must use type=module" }
    if (-not $pkg.scripts.check -or $pkg.scripts.check -notmatch "node --check gpt-prompter\.mjs") {
        Add-Failure "ai-orchestrator/node/package.json missing expected check script"
    }
}

$node = Get-NodeForCheck
if ($node) {
    foreach ($file in @("gpt-prompter.mjs", "gpt-summarizer.mjs", "model-router.mjs")) {
        $path = Join-Path $Global:NodeRoot $file
        & $node --check $path | Out-Null
        if ($LASTEXITCODE -ne 0) { Add-Failure "Node syntax check failed: $path" }
    }
}
else {
    Add-SoftWarning "node not found; skipped Node syntax check"
}

Write-Host "Checking marker contract..."
$schemaText = Read-TextIfExists -Path (Join-Path $Global:ContextRoot "OUTPUT_SCHEMAS.md")
$prompterText = Read-TextIfExists -Path (Join-Path $Global:NodeRoot "gpt-prompter.mjs")
$psPrompterText = Read-TextIfExists -Path (Join-Path $Global:OrchestratorRoot "scripts\run-gpt-prompter-lane.ps1")
$markers = @(
    "UPDATED_PROGRAM_STATUS",
    "UPDATED_ACTIVE_GOALS",
    "UPDATED_NEXT_STEPS",
    "UPDATED_LANE_STATUS",
    "NEXT_CODEX_PROMPT",
    "SHORT_COMMANDS_JSONL",
    "LONG_COMMANDS_MD",
    "NEXT_ACTION_PACKET",
    "CLAUDE_REVIEW_NEEDED",
    "BLOCKED_REASON",
    "ROADMAP_BACKLOG_UPDATES",
    "USER_ACTION_REQUIRED",
    "TASK_QUEUE_UPDATES",
    "MODEL_USED",
    "REASONING_SUMMARY"
)
foreach ($marker in $markers) {
    if ($schemaText -notmatch [regex]::Escape("${marker}_START")) { Add-Failure "OUTPUT_SCHEMAS missing ${marker}_START" }
    if ($prompterText -notmatch [regex]::Escape($marker)) { Add-Failure "gpt-prompter missing marker $marker" }
    if ($psPrompterText -notmatch [regex]::Escape($marker)) { Add-Failure "run-gpt-prompter-lane.ps1 does not handle marker $marker" }
}

Write-Host "Checking command schema consistency..."
$commandPolicy = Read-TextIfExists -Path (Join-Path $Global:ContextRoot "COMMAND_POLICY.md")
$runnerText = Read-TextIfExists -Path (Join-Path $Global:OrchestratorRoot "scripts\run-short-command-runner.ps1")
foreach ($field in @("id", "classification", "cwd", "command", "reason", "expected_output", "timeout_seconds")) {
    if ($commandPolicy -notmatch [regex]::Escape($field)) { Add-Failure "COMMAND_POLICY missing field $field" }
    if ($runnerText -notmatch [regex]::Escape($field)) { Add-Failure "run-short-command-runner missing field $field" }
}

Write-Host "Checking task and roadmap schemas..."
$taskQueue = Get-Content -LiteralPath (Join-Path $Global:StateRoot "TASK_QUEUE.json") -Raw | ConvertFrom-Json
$taskStatuses = @($taskQueue.tasks | ForEach-Object { $_.status })
if (-not ($taskStatuses -contains "ready" -or $taskStatuses -contains "blocked")) {
    Add-Failure "TASK_QUEUE.json must have at least one ready or blocked task"
}
foreach ($task in $taskQueue.tasks) {
    foreach ($field in @("id","lane","repo_path","status","priority","risk_level","requires_claude","title","objective","allowed_files","forbidden_files","required_tests","success_criteria","stop_conditions","notes")) {
        if (-not $task.PSObject.Properties.Name.Contains($field)) { Add-Failure "TASK_QUEUE task $($task.id) missing $field" }
    }
}

$roadmap = Get-Content -LiteralPath (Join-Path $Global:StateRoot "ROADMAP_BACKLOG.json") -Raw | ConvertFrom-Json
foreach ($item in $roadmap.items) {
    foreach ($field in @("id","lane","category","title","why_it_matters","profit_proximity","fake_edge_risk","implementation_effort","review_required","requires_user_help","user_help_needed","prerequisites","allowed_files","forbidden_files","success_criteria","stop_conditions","status","created_from")) {
        if (-not $item.PSObject.Properties.Name.Contains($field)) { Add-Failure "ROADMAP_BACKLOG item $($item.id) missing $field" }
    }
}

Write-Host "Checking anti-loop and no-giant-context policies..."
$prompterPolicy = Read-TextIfExists -Path (Join-Path $Global:ContextRoot "PROMPTER_POLICY.md")
if ($prompterPolicy -notmatch "must not output `"continue previous work`" as a task") { Add-Failure "PROMPTER_POLICY must ban broad continue prompts" }
if ($prompterPolicy -notmatch "No file should become a giant log") { Add-Failure "PROMPTER_POLICY must mention no giant logs" }
if ($psPrompterText -notmatch "LAST_GPT_INPUT_HASH") { Add-Failure "GPT prompter loop must persist input hash anti-loop guard" }
if ($schemaText -notmatch "ROADMAP_BACKLOG_UPDATES" -or $schemaText -notmatch "USER_ACTION_REQUIRED") {
    Add-Failure "OUTPUT_SCHEMAS must include roadmap and user-action markers"
}

Write-Host "Checking ignore/safety setup..."
$gitignore = Read-TextIfExists -Path (Join-Path $Global:RepoRoot ".gitignore")
foreach ($pattern in @("node_modules/", ".env", "*.pem", "*.key", "*.db", "ai-orchestrator/logs/")) {
    if ($gitignore -notmatch [regex]::Escape($pattern)) { Add-Failure ".gitignore missing $pattern" }
}
if (Test-Path -LiteralPath (Join-Path $Global:RepoRoot "ai-orchestrator\node_modules") -PathType Container) {
    Add-SoftWarning "ai-orchestrator/node_modules exists locally; .gitignore should keep it out of Git"
}

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Push-Location $Global:RepoRoot
    try {
        $oldErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $inside = & git rev-parse --is-inside-work-tree 2>$null
        $gitExit = $LASTEXITCODE
        $ErrorActionPreference = $oldErrorActionPreference
        if ($gitExit -eq 0 -and $inside -eq "true") {
            $trackedNodeModules = & git ls-files "*/node_modules/*" "node_modules/*" 2>$null
            if ($trackedNodeModules) { Add-Failure "node_modules appears in tracked files" }
            $status = & git status --short 2>$null
            foreach ($line in $status) {
                if ($line -match "(?i)(\.env|\.pem|\.key|secret)") {
                    Add-Failure "Possible secret-like file in git status: $line"
                }
            }
        }
        else {
            Add-SoftWarning "repo root is not a Git worktree; skipped tracked node_modules and staged secret checks"
        }
    }
    finally {
        Pop-Location
    }
}

Write-Host "Checking model configuration..."
foreach ($value in @($Global:GptCheapModel, $Global:GptDefaultModel, $Global:GptStrategicModel, $Global:ClaudeModel)) {
    if ([string]::IsNullOrWhiteSpace($value)) { Add-Failure "Model config contains an empty value" }
}

Write-Host "Checking tool availability..."
foreach ($tool in @("git", "python", "codex", "claude", "node")) {
    $found = Get-Command $tool -ErrorAction SilentlyContinue
    if (-not $found) {
        Add-SoftWarning "$tool not found on PATH"
    }
}

Write-Host ""
if ($warnings.Count -gt 0) {
    Write-Host "Warnings:"
    foreach ($warning in $warnings) { Write-Host "  WARN: $warning" }
}

if ($failures.Count -gt 0) {
    Write-Host "Failures:"
    foreach ($failure in $failures) { Write-Host "  FAIL: $failure" }
    throw "AI orchestrator setup check failed with $($failures.Count) failure(s)."
}

Write-Host "AI orchestrator setup check passed."
