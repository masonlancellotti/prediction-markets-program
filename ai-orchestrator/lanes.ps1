Set-StrictMode -Version 2.0

$Global:OrchestratorRoot = $PSScriptRoot
$Global:RepoRoot = (Resolve-Path (Join-Path $Global:OrchestratorRoot "..")).Path
$Global:LogsRoot = Join-Path $Global:OrchestratorRoot "logs"
$Global:ContextRoot = Join-Path $Global:OrchestratorRoot "context"
$Global:StateRoot = Join-Path $Global:OrchestratorRoot "state"
$Global:NodeRoot = Join-Path $Global:OrchestratorRoot "node"
$Global:StopFile = Join-Path $Global:OrchestratorRoot "STOP.txt"

# Editable supervisor settings.
$Global:ReviewEvery = 4
$Global:GptCheapModel = "gpt-5.4-nano"
$Global:GptDefaultModel = "gpt-5.4-mini"
$Global:GptStrategicModel = "gpt-5.5"
$Global:GptModel = $Global:GptDefaultModel
$Global:ClaudeModel = "opus"
$Global:CodexTimeoutSeconds = 900
$Global:MaxFailureRetries = 1
$Global:CommandPollSeconds = 5
$Global:PromptPollSeconds = 10
$Global:LoopSleepSeconds = $Global:PromptPollSeconds
$Global:GptPollSeconds = $Global:PromptPollSeconds
$Global:ClaudePollSeconds = $Global:PromptPollSeconds
$Global:ShortCommandPollSeconds = $Global:CommandPollSeconds
$Global:CommandResultsTailChars = 30000
$Global:FailureLogTailChars = 16000
$Global:ReviewDiffTailChars = 60000

# Editable lane config. Folder paths are relative to the repo root unless absolute.
$Global:Lanes = [ordered]@{
    weather = [pscustomobject]@{
        Name = "weather"
        Folder = "kalshi-weather-edge"
        Path = Join-Path $Global:RepoRoot "kalshi-weather-edge"
        Priority = "lower"
        Role = "Weather fair-value and market-making research lane."
    }
    relative_value = [pscustomobject]@{
        Name = "relative_value"
        Folder = "relative-value-scanner"
        Path = Join-Path $Global:RepoRoot "relative-value-scanner"
        Priority = "primary"
        Role = "Exact same-payoff and conservative paper-candidate lane."
    }
    graph = [pscustomobject]@{
        Name = "graph"
        Folder = "market-graph-consistency"
        Path = Join-Path $Global:RepoRoot "market-graph-consistency"
        Priority = "diagnostic"
        Role = "Diagnostic-only structural consistency hints lane."
    }
}

$Global:StableContextFiles = @(
    "PROJECT_CHARTER.md",
    "GLOBAL_GUARDRAILS.md",
    "COMMAND_POLICY.md",
    "PROMPTER_POLICY.md",
    "OUTPUT_SCHEMAS.md",
    "MODEL_ROUTING_POLICY.md"
)

$Global:StateFiles = @(
    "PROGRAM_STATUS.md",
    "ACTIVE_GOALS.md",
    "NEXT_STEPS.md",
    "DECISION_LOG.md",
    "TASK_QUEUE.json",
    "REVIEW_POLICY.json",
    "ROADMAP_BACKLOG.json",
    "FEATURE_IDEAS.md",
    "USER_ACTION_REQUIRED.md",
    "BLOCKER_ANALYSIS.md",
    "VENUE_EXPANSION_PLAN.md"
)

$Global:AiLoopFiles = @(
    "LANE_CONTEXT.md",
    "LANE_STATUS.md",
    "NEXT_CODEX_PROMPT.md",
    "LATEST_CODEX_SUMMARY.md",
    "LATEST_GPT_PROMPTER_OUTPUT.md",
    "LATEST_CLAUDE_REVIEW.md",
    "NEXT_ACTION_PACKET.md",
    "COMMANDS_SHORT_PENDING.jsonl",
    "COMMANDS_LONG_REVIEW.md",
    "COMMAND_RESULTS.md",
    "COMMANDS_DONE.jsonl",
    "RECOVERY_CONTEXT_PACKET.md",
    "FAILURE_LOG.md",
    "HEARTBEAT.json",
    "RUN_COUNTER.json"
)

$Global:RiskSensitivePathPatterns = @(
    "evaluator",
    "fee",
    "fees",
    "slippage",
    "settlement",
    "same_payoff",
    "paper_candidate",
    "paper-candidate",
    "matching",
    "matcher",
    "market_graph",
    "contract_relationship",
    "orderbook",
    "execution",
    "risk",
    "live",
    "backtest",
    "strategy",
    "strategies",
    "kalshi_client",
    "source_registry",
    "normalize"
)

function Get-AiLane {
    param([Parameter(Mandatory = $true)][string]$LaneName)

    if (-not $Global:Lanes.Contains($LaneName)) {
        throw "Unknown lane '$LaneName'. Valid lanes: $($Global:Lanes.Keys -join ', ')"
    }

    $lane = $Global:Lanes[$LaneName]
    if (-not (Test-Path -LiteralPath $lane.Path -PathType Container)) {
        throw "Lane '$LaneName' points to missing folder: $($lane.Path). Edit ai-orchestrator/lanes.ps1 if needed."
    }

    return $lane
}

function Get-ExistingAiLanes {
    $existing = @()
    foreach ($key in $Global:Lanes.Keys) {
        $lane = $Global:Lanes[$key]
        if (Test-Path -LiteralPath $lane.Path -PathType Container) {
            $existing += $lane
        }
    }
    return $existing
}

function Get-AiLoopPath {
    param([Parameter(Mandatory = $true)]$Lane)
    return (Join-Path $Lane.Path ".ai_loop")
}

function Get-AiLoopFile {
    param(
        [Parameter(Mandatory = $true)]$Lane,
        [Parameter(Mandatory = $true)][string]$FileName
    )
    return (Join-Path (Get-AiLoopPath -Lane $Lane) $FileName)
}

function Ensure-AiLoopDir {
    param([Parameter(Mandatory = $true)]$Lane)

    $loopPath = Get-AiLoopPath -Lane $Lane
    if (-not (Test-Path -LiteralPath $loopPath -PathType Container)) {
        New-Item -ItemType Directory -Path $loopPath -Force | Out-Null
    }
    return $loopPath
}

function Get-LaneLogRoot {
    param([Parameter(Mandatory = $true)][string]$LaneName)
    return (Join-Path $Global:LogsRoot $LaneName)
}

function Ensure-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Ensure-TextFileIfMissing {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Content = ""
    )

    $parent = Split-Path -Parent $Path
    if ($parent) { Ensure-Directory -Path $parent }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Set-Content -LiteralPath $Path -Value $Content -Encoding UTF8
    }
}

function Read-TextIfExists {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$TailChars = 0
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }

    $text = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if ($TailChars -gt 0 -and $text.Length -gt $TailChars) {
        return $text.Substring($text.Length - $TailChars)
    }
    return $text
}

function Set-Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Text = ""
    )

    $parent = Split-Path -Parent $Path
    if ($parent) { Ensure-Directory -Path $parent }
    Set-Content -LiteralPath $Path -Value $Text -Encoding UTF8
}

function Append-Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $parent = Split-Path -Parent $Path
    if ($parent) { Ensure-Directory -Path $parent }
    Add-Content -LiteralPath $Path -Value $Text -Encoding UTF8
}

function Get-UtcStamp {
    return (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
}

function Get-FileSafeStamp {
    return (Get-Date).ToString("yyyyMMdd_HHmmss")
}

function Get-ContentHash {
    param([Parameter(Mandatory = $true)][string]$Text)

    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hashBytes) -replace "-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Invoke-GitText {
    param(
        [Parameter(Mandatory = $true)][string]$Cwd,
        [Parameter(Mandatory = $true)][string[]]$Args
    )

    Push-Location $Cwd
    try {
        $output = & git @Args 2>&1
        if ($LASTEXITCODE -ne 0) {
            return "(git $($Args -join ' ') unavailable here: $($output -join [Environment]::NewLine))"
        }
        if ($null -eq $output -or $output.Count -eq 0) {
            return "(no output)"
        }
        return ($output -join [Environment]::NewLine)
    }
    catch {
        return "(git $($Args -join ' ') failed: $($_.Exception.Message))"
    }
    finally {
        Pop-Location
    }
}

function Get-GitSnapshot {
    param([Parameter(Mandatory = $true)]$Lane)

    $status = Invoke-GitText -Cwd $Lane.Path -Args @("status", "--short")
    $diffStat = Invoke-GitText -Cwd $Lane.Path -Args @("diff", "--stat")
    $diffNames = Invoke-GitText -Cwd $Lane.Path -Args @("diff", "--name-only")

    return [pscustomobject]@{
        Status = $status
        DiffStat = $diffStat
        DiffNames = $diffNames
        Hash = Get-ContentHash -Text ($status + "`n" + $diffStat + "`n" + $diffNames)
    }
}

function Test-HasMeaningfulCodeChange {
    param([Parameter(Mandatory = $true)][string]$DiffNames)

    if ([string]::IsNullOrWhiteSpace($DiffNames)) { return $false }
    if ($DiffNames -match "^\(git " -or $DiffNames.Trim() -eq "(no output)") { return $false }

    foreach ($line in ($DiffNames -split "`r?`n")) {
        $name = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -match "\.(py|ps1|mjs|js|ts|tsx|json|yaml|yml|toml|sql)$") { return $true }
    }
    return $false
}

function Test-HasRiskSensitiveChange {
    param([Parameter(Mandatory = $true)][string]$DiffNames)

    if ([string]::IsNullOrWhiteSpace($DiffNames)) { return $false }
    if ($DiffNames -match "^\(git " -or $DiffNames.Trim() -eq "(no output)") { return $false }

    foreach ($pattern in $Global:RiskSensitivePathPatterns) {
        if ($DiffNames -match [regex]::Escape($pattern)) {
            return $true
        }
    }
    return $false
}

function Set-LaneHeartbeat {
    param(
        [Parameter(Mandatory = $true)]$Lane,
        [Parameter(Mandatory = $true)][string]$Role,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Detail = ""
    )

    $path = Get-AiLoopFile -Lane $Lane -FileName "HEARTBEAT.json"
    $payload = [ordered]@{
        lane = $Lane.Name
        role = $Role
        status = $Status
        detail = $Detail
        updated_at = Get-UtcStamp
    } | ConvertTo-Json -Depth 5
    Set-Text -Path $path -Text $payload
}

function Get-AndIncrementRunCounter {
    param(
        [Parameter(Mandatory = $true)]$Lane,
        [Parameter(Mandatory = $true)][string]$CounterName
    )

    $path = Get-AiLoopFile -Lane $Lane -FileName "RUN_COUNTER.json"
    $data = [ordered]@{}
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try {
            $existing = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            foreach ($prop in $existing.PSObject.Properties) {
                $data[$prop.Name] = $prop.Value
            }
        }
        catch {
            $data = [ordered]@{}
        }
    }

    if (-not $data.Contains($CounterName)) {
        $data[$CounterName] = 0
    }
    $data[$CounterName] = [int]$data[$CounterName] + 1
    $data["updated_at"] = Get-UtcStamp
    Set-Text -Path $path -Text ($data | ConvertTo-Json -Depth 5)
    return [int]$data[$CounterName]
}

function Get-LaneSeedContent {
    param(
        [Parameter(Mandatory = $true)]$Lane,
        [Parameter(Mandatory = $true)][string]$FileName
    )

    switch ($FileName) {
        "LANE_CONTEXT.md" {
            return @"
# Lane Context: $($Lane.Name)

Folder: `$($Lane.Folder)`
Role: $($Lane.Role)
Priority: $($Lane.Priority)

Use this lane only for its stated role. Do not add live trading, order submission, auth/account/private-key/signing/wallet/deploy/git push logic.

Codex summaries for this lane must include: task id, files changed, commands run, tests run, pass/fail, paper candidates count, risk flags, whether Claude review is needed, recommended next task, and whether docs/state were updated.
"@
        }
        "LANE_STATUS.md" { return "# Lane Status: $($Lane.Name)`r`n`r`nStatus: WATCH`r`n" }
        "NEXT_CODEX_PROMPT.md" {
            $charterPath = Join-Path $Global:OrchestratorRoot "context\PROJECT_CHARTER.md"
            return @"
# Next Codex Prompt

Read $charterPath, this lane's `.ai_loop/LANE_CONTEXT.md`, `.ai_loop/LANE_STATUS.md`, and the current state files. Do one small safe task only, then update the lane handoff files.
"@
        }
        "LATEST_CODEX_SUMMARY.md" { return "# Latest Codex Summary`r`n" }
        "LATEST_GPT_PROMPTER_OUTPUT.md" { return "# Latest GPT Prompter Output`r`n" }
        "LATEST_CLAUDE_REVIEW.md" { return "# Latest Claude Review`r`n" }
        "NEXT_ACTION_PACKET.md" { return "# Next Action Packet`r`n" }
        "COMMANDS_SHORT_PENDING.jsonl" { return "" }
        "COMMANDS_LONG_REVIEW.md" { return "# Long/Manual Command Requests`r`n" }
        "COMMAND_RESULTS.md" { return "# Command Results`r`n" }
        "COMMANDS_DONE.jsonl" { return "" }
        "RECOVERY_CONTEXT_PACKET.md" { return "# Recovery Context Packet`r`n" }
        "FAILURE_LOG.md" { return "# Failure Log`r`n" }
        "HEARTBEAT.json" {
            return ([ordered]@{
                lane = $Lane.Name
                role = "init"
                status = "initialized"
                detail = "Seed heartbeat."
                updated_at = Get-UtcStamp
            } | ConvertTo-Json -Depth 5)
        }
        "RUN_COUNTER.json" {
            return ([ordered]@{
                codex = 0
                gpt_prompter = 0
                claude = 0
                updated_at = Get-UtcStamp
            } | ConvertTo-Json -Depth 5)
        }
        default { return "" }
    }
}

function Ensure-AiLoopFileSet {
    param([Parameter(Mandatory = $true)]$Lane)

    $loopPath = Ensure-AiLoopDir -Lane $Lane
    foreach ($fileName in $Global:AiLoopFiles) {
        Ensure-TextFileIfMissing -Path (Join-Path $loopPath $fileName) -Content (Get-LaneSeedContent -Lane $Lane -FileName $fileName)
    }
    return $loopPath
}

function Get-DelimitedSection {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$StartMarker,
        [Parameter(Mandatory = $true)][string]$EndMarker
    )

    $pattern = "(?s)$([regex]::Escape($StartMarker))\s*(.*?)\s*$([regex]::Escape($EndMarker))"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}
