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
    "AUTONOMY_MODE.json",
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
$psPrompterOncePath = Join-Path $Global:OrchestratorRoot "scripts\run-gpt-prompter-once.ps1"
Assert-File -Path $psPrompterOncePath
$psPrompterOnceText = Read-TextIfExists -Path $psPrompterOncePath
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
    if ($psPrompterOnceText -notmatch [regex]::Escape($marker)) { Add-Failure "run-gpt-prompter-once.ps1 does not handle marker $marker" }
}
if ($psPrompterText -notmatch '\[switch\]\s*\$Once') {
    Add-Failure "run-gpt-prompter-lane.ps1 must expose a -Once switch"
}
if ($psPrompterText -notmatch '\[int\]\s*\$TimeoutSeconds') {
    Add-Failure "run-gpt-prompter-lane.ps1 must expose a -TimeoutSeconds parameter"
}
if ($psPrompterText -notmatch "Node GPT prompter timed out") {
    Add-Failure "run-gpt-prompter-lane.ps1 must hard-timeout the one-shot Node child process"
}
if ($psPrompterText -notmatch "Invoke-GptPrompterCycle") {
    Add-Failure "run-gpt-prompter-lane.ps1 must have a one-cycle prompter function"
}
if ($psPrompterOnceText -notmatch '\[int\]\s*\$TimeoutSeconds') {
    Add-Failure "run-gpt-prompter-once.ps1 must expose a -TimeoutSeconds parameter"
}
if ($psPrompterOnceText -notmatch "Start-Process" -and $psPrompterOnceText -notmatch "System.Diagnostics.Process") {
    Add-Failure "run-gpt-prompter-once.ps1 must use Start-Process -PassThru or equivalent child-process control"
}
if ($psPrompterOnceText -notmatch "Stop-ProcessTree" -or $psPrompterOnceText -notmatch "timed out after") {
    Add-Failure "run-gpt-prompter-once.ps1 must kill timed-out node process trees"
}
if ($psPrompterOnceText -match "ConvertTo-Json") {
    Add-Failure "run-gpt-prompter-once.ps1 must not use ConvertTo-Json for one-shot packet serialization"
}
if ($psPrompterOnceText -notmatch "--packet-text") {
    Add-Failure "run-gpt-prompter-once.ps1 must pass a plain text packet via --packet-text"
}
if ($psPrompterOnceText -notmatch "NoApiSmoke" -or $psPrompterOnceText -notmatch "DebugTrace") {
    Add-Failure "run-gpt-prompter-once.ps1 must expose -NoApiSmoke and -DebugTrace"
}
if ($prompterText -notmatch "AbortController" -or $prompterText -notmatch "GPT_PROMPTER_API_TIMEOUT_SECONDS") {
    Add-Failure "gpt-prompter.mjs must use AbortController with GPT_PROMPTER_API_TIMEOUT_SECONDS"
}
if ($prompterText -notmatch "--packet-text") {
    Add-Failure "gpt-prompter.mjs must accept --packet-text"
}
if ($prompterText -notmatch "fileURLToPath" -or $prompterText -notmatch "path.resolve") {
    Add-Failure "gpt-prompter.mjs must use a Windows-safe ESM entrypoint check"
}
if ($prompterText -notmatch "no-api-smoke") {
    Add-Failure "gpt-prompter.mjs must expose a direct --no-api-smoke CLI path"
}
if ($node) {
    Write-Host "Checking gpt-prompter direct no-api smoke..."
    $smokeRoot = Join-Path $Global:LogsRoot "setup-smoke"
    Ensure-Directory -Path $smokeRoot
    $smokeId = Get-FileSafeStamp
    $smokePacket = Join-Path $smokeRoot "gpt_prompter_packet_$smokeId.txt"
    $smokeOutput = Join-Path $smokeRoot "gpt_prompter_output_$smokeId.md"
    $smokeErr = Join-Path $smokeRoot "gpt_prompter_stderr_$smokeId.log"
    Set-Text -Path $smokePacket -Text "setup smoke packet: verify CLI main writes marker-complete output without API"
    $prompterPath = Join-Path $Global:NodeRoot "gpt-prompter.mjs"
    & $node $prompterPath --packet-text $smokePacket --lane-name setup_smoke --lane-path $Global:RepoRoot --output $smokeOutput --no-api-smoke 2> $smokeErr
    if ($LASTEXITCODE -ne 0) {
        $errText = Read-TextIfExists -Path $smokeErr -TailChars 1000
        Add-Failure "gpt-prompter.mjs direct --no-api-smoke exited $LASTEXITCODE. stderr: $errText"
    }
    elseif (-not (Test-Path -LiteralPath $smokeOutput -PathType Leaf)) {
        Add-Failure "gpt-prompter.mjs direct --no-api-smoke did not write output"
    }
    else {
        $smokeText = Read-TextIfExists -Path $smokeOutput
        foreach ($marker in @("NEXT_CODEX_PROMPT", "NEXT_ACTION_PACKET", "MODEL_USED", "REASONING_SUMMARY")) {
            if ($smokeText -notmatch [regex]::Escape("${marker}_START") -or $smokeText -notmatch [regex]::Escape("${marker}_END")) {
                Add-Failure "gpt-prompter.mjs direct --no-api-smoke output missing marker $marker"
            }
        }
    }
}

foreach ($nodeFile in @("gpt-prompter.mjs", "gpt-summarizer.mjs")) {
    $nodeText = Read-TextIfExists -Path (Join-Path $Global:NodeRoot $nodeFile)
    foreach ($samplingParam in @("temperature", "top_p", "presence_penalty", "frequency_penalty")) {
        if ($nodeText -match "${samplingParam}\s*:") {
            Add-Failure "$nodeFile must omit unsupported Responses API sampling parameter: $samplingParam"
        }
    }
}

Write-Host "Checking command schema consistency..."
$commandPolicy = Read-TextIfExists -Path (Join-Path $Global:ContextRoot "COMMAND_POLICY.md")
$runnerText = Read-TextIfExists -Path (Join-Path $Global:OrchestratorRoot "scripts\run-short-command-runner.ps1")
foreach ($field in @("id", "classification", "cwd", "command", "reason", "expected_output", "timeout_seconds")) {
    if ($commandPolicy -notmatch [regex]::Escape($field)) { Add-Failure "COMMAND_POLICY missing field $field" }
    if ($runnerText -notmatch [regex]::Escape($field)) { Add-Failure "run-short-command-runner missing field $field" }
}
foreach ($field in @("id", "lane", "cwd", "command", "why_needed", "blocking_task_id", "expected_output", "risk_reason", "timeout_suggestion", "status: OPEN | RUNNING | DONE | SKIPPED")) {
    if ($commandPolicy -notmatch [regex]::Escape($field)) { Add-Failure "COMMAND_POLICY missing long/manual field or lifecycle text: $field" }
}
foreach ($term in @("WAITING_USER_COMMAND", "BLOCKED_USER", "COMMAND_RESULTS.md", "run-manual-command-and-log.ps1")) {
    if ($commandPolicy -notmatch [regex]::Escape($term)) { Add-Failure "COMMAND_POLICY missing manual command workflow term: $term" }
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
if ($prompterPolicy -notmatch "ROADMAP_BACKLOG" -or $prompterPolicy -notmatch "not directly to Codex prompts") { Add-Failure "PROMPTER_POLICY must keep new ideas in ROADMAP_BACKLOG instead of direct Codex prompts" }
if ($psPrompterText -notmatch "LAST_GPT_INPUT_HASH") { Add-Failure "GPT prompter loop must persist input hash anti-loop guard" }
if ($schemaText -notmatch "ROADMAP_BACKLOG_UPDATES" -or $schemaText -notmatch "USER_ACTION_REQUIRED") {
    Add-Failure "OUTPUT_SCHEMAS must include roadmap and user-action markers"
}
if ($schemaText -notmatch "REASONING_SUMMARY_START" -or $schemaText -notmatch "GPT_PROMPTER_REASONING_SUMMARY" -or $schemaText -notmatch "UNCHANGED by default") {
    Add-Failure "OUTPUT_SCHEMAS must document REASONING_SUMMARY marker and disabled-by-default behavior"
}
foreach ($script in @("test-next-codex-prompt.ps1","test-task-queue.ps1","test-lane-file-scope.ps1","test-review-gating.ps1","test-autonomy-preflight.ps1")) {
    Assert-File -Path (Join-Path $Global:OrchestratorRoot "scripts\$script")
}
if ($psPrompterText -notmatch "test-next-codex-prompt.ps1") {
    $codexRunnerText = Read-TextIfExists -Path (Join-Path $Global:OrchestratorRoot "scripts\run-codex-lane.ps1")
    if ($codexRunnerText -notmatch "test-next-codex-prompt.ps1") {
        Add-Failure "run-codex-lane.ps1 must validate NEXT_CODEX_PROMPT before launching Codex"
    }
}
$readmeText = Read-TextIfExists -Path (Join-Path $Global:OrchestratorRoot "README_AI_ORCHESTRATOR.md")
if ($readmeText -notmatch "run-manual-command-and-log\.ps1" -or $readmeText -notmatch "CommandId" -or $readmeText -notmatch "WAITING_USER_COMMAND") {
    Add-Failure "README must document manual command logging with command id and WAITING_USER_COMMAND"
}
if ($readmeText -notmatch "GPT_PROMPTER_REASONING_SUMMARY" -or $readmeText -notmatch "costs output tokens") {
    Add-Failure "README must document GPT_PROMPTER_REASONING_SUMMARY cost behavior"
}
if ($prompterPolicy -notmatch "independent ready tasks exist" -or $prompterPolicy -notmatch "WAITING_USER_COMMAND" -or $prompterPolicy -notmatch "BLOCKED_USER") {
    Add-Failure "PROMPTER_POLICY must document manual command wait/continue behavior"
}
if ($prompterPolicy -notmatch "GPT_PROMPTER_REASONING_SUMMARY=0" -or $prompterPolicy -notmatch "visible generated text") {
    Add-Failure "PROMPTER_POLICY must document disabled routine reasoning summaries"
}

Write-Host "Checking ignore/safety setup..."
Ensure-Directory -Path $Global:LogsRoot
if (-not (Test-Path -LiteralPath $Global:LogsRoot -PathType Container)) {
    Add-Failure "logs directory could not be created: $Global:LogsRoot"
}
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
if ($node) {
    Write-Host "Checking model router decisions..."
    $routerSmokeRoot = Join-Path $Global:LogsRoot "setup-smoke"
    Ensure-Directory -Path $routerSmokeRoot
    $routerCheckPath = Join-Path $routerSmokeRoot "model_router_check_$(Get-FileSafeStamp).mjs"
    $routerCheckOutput = Join-Path $routerSmokeRoot "model_router_check_$(Get-FileSafeStamp).json"
    $routerCheckError = Join-Path $routerSmokeRoot "model_router_check_$(Get-FileSafeStamp).err"
    $routerScript = @"
import { chooseModel } from "../../node/model-router.mjs";

const env = {
  GPT_CHEAP_MODEL: "$($Global:GptCheapModel)",
  GPT_DEFAULT_MODEL: "$($Global:GptDefaultModel)",
  GPT_STRATEGIC_MODEL: "$($Global:GptStrategicModel)"
};

const cases = [
  {
    name: "routine",
    packet: {
      text: "Stable guardrails mention risk, matching, same_payoff, review, evaluator, settlement, and fees. Dynamic program state: routine next prompt generation for relative_value.",
      laneStatus: "Status: ACTIVE"
    },
    expected: env.GPT_DEFAULT_MODEL
  },
  {
    name: "repeated_failure",
    packet: {
      text: "Dynamic program state. Failure count: 2. Retry failed on same task.",
      laneStatus: "Status: ACTIVE"
    },
    expected: env.GPT_STRATEGIC_MODEL
  },
  {
    name: "cheap_summary",
    packet: {
      triggerReason: "log tail tiny summary and command extraction"
    },
    expected: env.GPT_CHEAP_MODEL
  }
];

const results = cases.map((item) => ({ name: item.name, expected: item.expected, actual: chooseModel(item.packet, env).model }));
const failures = results.filter((item) => item.actual !== item.expected);
process.stdout.write(JSON.stringify({ results, failures }, null, 2));
if (failures.length > 0) {
  process.exitCode = 1;
}
"@
    Set-Text -Path $routerCheckPath -Text $routerScript
    & $node $routerCheckPath > $routerCheckOutput 2> $routerCheckError
    if ($LASTEXITCODE -ne 0) {
        $outText = Read-TextIfExists -Path $routerCheckOutput -TailChars 2000
        $errText = Read-TextIfExists -Path $routerCheckError -TailChars 1000
        Add-Failure "model-router decision smoke failed. stdout: $outText stderr: $errText"
    }
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
