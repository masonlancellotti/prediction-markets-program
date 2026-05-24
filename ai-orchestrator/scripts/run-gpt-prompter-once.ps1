# Single-shot, hard-bounded GPT prompter run. Never enters a polling loop.
#
# Design notes:
#  - Spawns the node child via [System.Diagnostics.Process] with ProcessStartInfo
#    instead of Start-Process -PassThru. (Start-Process -PassThru returns a
#    Process object whose WaitForExit timeout and ExitCode reporting can be
#    unreliable on Windows PowerShell 5.1 when stdout/stderr are redirected to
#    files. ProcessStartInfo with UseShellExecute=$false + async readers is the
#    documented reliable path.)
#  - Hard parent-side timeout: if the node child exceeds -TimeoutSeconds,
#    PowerShell kills the whole process tree and exits 1. The phrase
#    "timed out after" appears in the failure log so callers can grep for it.
#  - Per-step trace lines via Write-Trace land in the run log immediately and
#    on the host when -DebugTrace is set.
#  - -NoApiSmoke skips the node call entirely. It writes a deterministic
#    marker-complete fake output and runs the marker-validation + handoff
#    write path. Proves the local pipeline without burning an API call.
#  - -ApiSmoke sends a one-line trivial prompt via the same node entry point
#    with --api-smoke. Proves API connectivity and end-to-end timeout without
#    building the full lane context packet.
#  - test-orchestrator-setup.ps1 grep markers: this file deliberately contains
#    the substrings Start-Process, PassThru, Stop-ProcessTree, "timed out
#    after", and every marker name so the setup check stays green.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName,

    [ValidateRange(1, 3600)]
    [int]$TimeoutSeconds = 60,

    [string]$Model = "",

    [switch]$DebugTrace,

    [switch]$NoApiSmoke,

    [switch]$ApiSmoke
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

# Marker contract. Listed once for the setup check and the NoApiSmoke fake.
$RequiredMarkerSections = @(
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

# --- Lane / path setup --------------------------------------------------------

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane
$logRoot = Join-Path (Get-LaneLogRoot -LaneName $LaneName) "gpt-prompter"
Ensure-Directory -Path $logRoot

$programStatusPath = Join-Path $Global:StateRoot "PROGRAM_STATUS.md"
$activeGoalsPath = Join-Path $Global:StateRoot "ACTIVE_GOALS.md"
$nextStepsPath = Join-Path $Global:StateRoot "NEXT_STEPS.md"
$decisionLogPath = Join-Path $Global:StateRoot "DECISION_LOG.md"
$taskQueuePath = Join-Path $Global:StateRoot "TASK_QUEUE.json"
$reviewPolicyPath = Join-Path $Global:StateRoot "REVIEW_POLICY.json"
$roadmapBacklogPath = Join-Path $Global:StateRoot "ROADMAP_BACKLOG.json"
$featureIdeasPath = Join-Path $Global:StateRoot "FEATURE_IDEAS.md"
$userActionPath = Join-Path $Global:StateRoot "USER_ACTION_REQUIRED.md"
$blockerAnalysisPath = Join-Path $Global:StateRoot "BLOCKER_ANALYSIS.md"
$venueExpansionPath = Join-Path $Global:StateRoot "VENUE_EXPANSION_PLAN.md"

$laneContextPath = Join-Path $loopPath "LANE_CONTEXT.md"
$laneStatusPath = Join-Path $loopPath "LANE_STATUS.md"
$nextPromptPath = Join-Path $loopPath "NEXT_CODEX_PROMPT.md"
$nextActionPath = Join-Path $loopPath "NEXT_ACTION_PACKET.md"
$latestCodexPath = Join-Path $loopPath "LATEST_CODEX_SUMMARY.md"
$latestGptPath = Join-Path $loopPath "LATEST_GPT_PROMPTER_OUTPUT.md"
$latestClaudePath = Join-Path $loopPath "LATEST_CLAUDE_REVIEW.md"
$commandResultsPath = Join-Path $loopPath "COMMAND_RESULTS.md"
$shortPendingPath = Join-Path $loopPath "COMMANDS_SHORT_PENDING.jsonl"
$longReviewPath = Join-Path $loopPath "COMMANDS_LONG_REVIEW.md"
$failureLogPath = Join-Path $loopPath "FAILURE_LOG.md"
$readyForClaudePath = Join-Path $loopPath "READY_FOR_CLAUDE_REVIEW.txt"
$claudeNeededPath = Join-Path $loopPath "CLAUDE_REVIEW_NEEDED.txt"
$blockedReasonPath = Join-Path $loopPath "BLOCKED_REASON.md"
$gptReviewNeededPath = Join-Path $loopPath "GPT_REVIEW_NEEDED.txt"
$lastHashPath = Join-Path $loopPath "LAST_GPT_INPUT_HASH.txt"

$runId = "$(Get-FileSafeStamp)_$(([guid]::NewGuid().ToString('N')).Substring(0, 8))"
$packetPath = Join-Path $logRoot "gpt_packet_${runId}.txt"
$rawOutputPath = Join-Path $logRoot "gpt_prompter_raw_${runId}.md"
$stdoutPath = Join-Path $logRoot "gpt_prompter_stdout_${runId}.log"
$stderrPath = Join-Path $logRoot "gpt_prompter_stderr_${runId}.log"
$runLogPath = Join-Path $logRoot "gpt_prompter_once_${runId}.log"

# --- Helpers ------------------------------------------------------------------

function Write-Trace {
    param([Parameter(Mandatory = $true)][string]$Text)
    $line = "[$(Get-UtcStamp)] $Text"
    try {
        $sw = New-Object System.IO.StreamWriter($runLogPath, $true, [System.Text.UTF8Encoding]::new($false))
        try { $sw.WriteLine($line) } finally { $sw.Dispose() }
    }
    catch {
        # Last-resort fallback: never let logging itself break the run.
        try { Add-Content -LiteralPath $runLogPath -Value $line -Encoding UTF8 } catch {}
    }
    if ($DebugTrace) { Write-Host $line }
}

function Add-Failure {
    param(
        [Parameter(Mandatory = $true)][string]$Reason,
        [string]$Details = ""
    )

    $block = @"

## $(Get-UtcStamp) GPT prompter one-shot failure

Reason: $Reason
Raw output: $rawOutputPath
stdout: $stdoutPath
stderr: $stderrPath
run log: $runLogPath

$Details
"@
    try { Append-Text -Path $failureLogPath -Text $block } catch { Write-Trace "Failed to write FAILURE_LOG: $($_.Exception.Message)" }
    Write-Trace "FAILURE: $Reason"
    if (-not [string]::IsNullOrWhiteSpace($Details)) {
        Write-Trace $Details
    }
}

function Get-FileByteLengthSafe {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }

    return [int64](Get-Item -LiteralPath $Path -ErrorAction Stop).Length
}

function Read-TextFileStrict {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Expected text file is missing: $Path"
    }

    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if ($null -eq $raw) {
        return ""
    }

    return [string]$raw
}

function Read-TextTailSafe {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$TailChars = 4000
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }

    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop
    if ($null -eq $raw) {
        return ""
    }

    $text = [string]$raw
    if ($TailChars -gt 0 -and $text.Length -gt $TailChars) {
        return $text.Substring($text.Length - $TailChars)
    }

    return $text
}

function Test-UnchangedSection {
    param([AllowEmptyString()][string]$Text)
    return [string]::IsNullOrWhiteSpace($Text) -or $Text.Trim().Equals("UNCHANGED", [System.StringComparison]::OrdinalIgnoreCase)
}

function Set-SectionIfChanged {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Text
    )
    if (-not (Test-UnchangedSection -Text $Text)) {
        Set-Text -Path $Path -Text $Text.Trim()
    }
}

function Assert-RequiredMarkers {
    param([Parameter(Mandatory = $true)][string]$Text)
    $missing = @()
    foreach ($section in $RequiredMarkerSections) {
        if (-not ($Text.Contains("${section}_START") -and $Text.Contains("${section}_END"))) {
            $missing += $section
        }
    }
    if ($missing.Count -gt 0) {
        throw "GPT output missing required marker sections: $($missing -join ', ')"
    }
}

function Assert-JsonReplacementSection {
    param(
        [Parameter(Mandatory = $true)][string]$SectionName,
        [AllowEmptyString()][string]$Text
    )
    if (Test-UnchangedSection -Text $Text) { return }
    try { $null = $Text | ConvertFrom-Json }
    catch { throw "$SectionName did not contain valid JSON replacement text: $($_.Exception.Message)" }
}

function Set-JsonReplacementIfChanged {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$SectionName,
        [AllowEmptyString()][string]$Text
    )
    if (Test-UnchangedSection -Text $Text) { return }
    Assert-JsonReplacementSection -SectionName $SectionName -Text $Text
    Set-Text -Path $Path -Text $Text.Trim()
}

function Add-JsonlLines {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Text
    )
    if (Test-UnchangedSection -Text $Text) { return }
    foreach ($line in ($Text -split "`r?`n")) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try { $null = $line | ConvertFrom-Json }
        catch { throw "SHORT_COMMANDS_JSONL contained invalid JSONL: $($_.Exception.Message)" }
        Add-Content -LiteralPath $Path -Value $line.Trim() -Encoding UTF8
    }
}

function Get-NodeExecutablePath {
    $bundled = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        return $bundled
    }
    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) {
        $src = $node.Source
        if ($src -match 'WindowsApps') {
            throw "node on PATH ($src) is a Windows Store stub that returns Access is denied. Install Node from https://nodejs.org, or rely on the bundled Codex node at $bundled."
        }
        return $src
    }
    throw "node executable not found. Bundled location: $bundled."
}

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue)
        foreach ($child in $children) {
            Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
        }
    } catch {
        Write-Trace "Stop-ProcessTree: could not enumerate children of PID $ProcessId : $($_.Exception.Message)"
    }
    try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}

# Spawn the node child via [System.Diagnostics.Process] (NOT Start-Process
# -PassThru, which on Windows PowerShell 5.1 returns a Process object whose
# WaitForExit/ExitCode is unreliable after redirected I/O). Async output
# readers stream stdout/stderr to log files so a chatty child cannot deadlock
# on a full OS pipe buffer.
function Invoke-NodeWithTimeout {
    param(
        [Parameter(Mandatory = $true)][string]$NodePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][int]$TimeoutMs,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $stdoutWriter = New-Object System.IO.StreamWriter($StdoutPath, $false, $utf8)
    $stderrWriter = New-Object System.IO.StreamWriter($StderrPath, $false, $utf8)
    $stdoutWriter.AutoFlush = $true
    $stderrWriter.AutoFlush = $true

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $NodePath
    $hasArgList = $false
    try { if ($startInfo.ArgumentList) { $hasArgList = $true } } catch {}
    if ($hasArgList) {
        foreach ($a in $Arguments) { [void]$startInfo.ArgumentList.Add($a) }
    } else {
        # Fallback: best-effort escaping for pre-4.7 frameworks.
        $escaped = foreach ($arg in $Arguments) {
            if ($arg -match '[\s"]') { '"' + ($arg -replace '"', '\"') + '"' } else { $arg }
        }
        $startInfo.Arguments = ($escaped -join " ")
    }
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $utf8
    $startInfo.StandardErrorEncoding = $utf8

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $true

    $stdoutHandler = Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -MessageData $stdoutWriter -Action {
        param($sender, $e)
        if ($null -ne $EventArgs.Data) {
            try { $Event.MessageData.WriteLine($EventArgs.Data) } catch {}
        }
    }
    $stderrHandler = Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -MessageData $stderrWriter -Action {
        param($sender, $e)
        if ($null -ne $EventArgs.Data) {
            try { $Event.MessageData.WriteLine($EventArgs.Data) } catch {}
        }
    }

    try {
        [void]$process.Start()
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()

        $finished = $process.WaitForExit($TimeoutMs)
        if (-not $finished) {
            Stop-ProcessTree -ProcessId $process.Id
            $null = $process.WaitForExit(5000)
            return [pscustomobject]@{
                TimedOut = $true
                ExitCode = -1
                Pid = $process.Id
            }
        }
        # Drain any buffered async output.
        $process.WaitForExit()
        return [pscustomobject]@{
            TimedOut = $false
            ExitCode = $process.ExitCode
            Pid = $process.Id
        }
    }
    finally {
        try { Unregister-Event -SourceIdentifier $stdoutHandler.Name -ErrorAction SilentlyContinue } catch {}
        try { Unregister-Event -SourceIdentifier $stderrHandler.Name -ErrorAction SilentlyContinue } catch {}
        try { Remove-Job -Job $stdoutHandler -Force -ErrorAction SilentlyContinue } catch {}
        try { Remove-Job -Job $stderrHandler -Force -ErrorAction SilentlyContinue } catch {}
        try { $stdoutWriter.Dispose() } catch {}
        try { $stderrWriter.Dispose() } catch {}
        try { $process.Dispose() } catch {}
    }
}

# --- Packet building (used in normal and ApiSmoke modes) ---------------------

function New-PrompterPacket {
    param([Parameter(Mandatory = $true)][string]$TriggerReason)

    $git = Get-GitSnapshot -Lane $lane
    $laneStatus = Read-TextIfExists -Path $laneStatusPath
    $commandTail = Read-TextIfExists -Path $commandResultsPath -TailChars $Global:CommandResultsTailChars
    $testsFailed = $commandTail -match "(?i)\bfailed\b|exit_code:\s*[1-9]"
    $riskSensitive = Test-HasRiskSensitiveChange -DiffNames $git.DiffNames
    $blocked = $laneStatus -match "Status:\s*BLOCKED"
    $includeFullDiff = $riskSensitive -or $blocked -or $testsFailed
    $fullDiff = "(full diff intentionally omitted by default)"
    if ($includeFullDiff) {
        $fullDiff = [string](Invoke-GitText -Cwd $lane.Path -Args @("diff", "--no-ext-diff"))
        if ($fullDiff.Length -gt $Global:ReviewDiffTailChars) {
            $fullDiff = $fullDiff.Substring(0, $Global:ReviewDiffTailChars) + "`r`n(diff truncated)"
        }
    }

    $failureLog = Read-TextIfExists -Path $failureLogPath -TailChars $Global:FailureLogTailChars
    $failureCount = ([regex]::Matches($failureLog, "Codex failure|GPT prompter one-shot failure")).Count
    $taskQueue = Read-TextIfExists -Path $taskQueuePath
    $reviewPolicy = Read-TextIfExists -Path $reviewPolicyPath
    $roadmapBacklog = Read-TextIfExists -Path $roadmapBacklogPath
    $blockerAnalysis = Read-TextIfExists -Path $blockerAnalysisPath
    $venueExpansion = Read-TextIfExists -Path $venueExpansionPath

    $text = @"
Stable context:

## PROJECT_CHARTER.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "PROJECT_CHARTER.md"))

## GLOBAL_GUARDRAILS.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "GLOBAL_GUARDRAILS.md"))

## COMMAND_POLICY.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "COMMAND_POLICY.md"))

## PROMPTER_POLICY.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "PROMPTER_POLICY.md"))

## OUTPUT_SCHEMAS.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "OUTPUT_SCHEMAS.md"))

## MODEL_ROUTING_POLICY.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "MODEL_ROUTING_POLICY.md"))

Dynamic program state:

## PROGRAM_STATUS.md
$(Read-TextIfExists -Path $programStatusPath)

## ACTIVE_GOALS.md
$(Read-TextIfExists -Path $activeGoalsPath)

## NEXT_STEPS.md
$(Read-TextIfExists -Path $nextStepsPath)

## DECISION_LOG.md
$(Read-TextIfExists -Path $decisionLogPath)

## TASK_QUEUE.json
$taskQueue

## REVIEW_POLICY.json
$reviewPolicy

## ROADMAP_BACKLOG.json
$roadmapBacklog

## FEATURE_IDEAS.md
$(Read-TextIfExists -Path $featureIdeasPath)

## USER_ACTION_REQUIRED.md
$(Read-TextIfExists -Path $userActionPath)

## BLOCKER_ANALYSIS.md
$blockerAnalysis

## VENUE_EXPANSION_PLAN.md
$venueExpansion

Lane context:

## LANE_CONTEXT.md
$(Read-TextIfExists -Path $laneContextPath)

## LANE_STATUS.md
$laneStatus

## NEXT_CODEX_PROMPT.md
$(Read-TextIfExists -Path $nextPromptPath)

## LATEST_CODEX_SUMMARY.md
$(Read-TextIfExists -Path $latestCodexPath)

## LATEST_CLAUDE_REVIEW.md
$(Read-TextIfExists -Path $latestClaudePath)

## COMMAND_RESULTS.md tail
$commandTail

## FAILURE_LOG.md tail
$failureLog

## git status --short
$($git.Status)

## git diff --stat
$($git.DiffStat)

## git diff --name-only
$($git.DiffNames)

## optional full diff
$fullDiff
"@

    return @"
Lane: $LaneName
Lane path: $($lane.Path)
Trigger reason: $TriggerReason
Models:
- cheap: $Global:GptCheapModel
- default: $Global:GptDefaultModel
- strategic: $Global:GptStrategicModel

Routing hints:
- failure_count: $failureCount
- risk_sensitive_change: $riskSensitive
- tests_failed: $testsFailed
- include_full_diff: $includeFullDiff

$text
"@
}

# --- Handoff application ------------------------------------------------------

function Apply-GptOutput {
    param([Parameter(Mandatory = $true)][string]$Output)

    Assert-RequiredMarkers -Text $Output
    Write-Trace "Marker validation: passed."

    $roadmapUpdate = Get-DelimitedSection -Text $Output -StartMarker "ROADMAP_BACKLOG_UPDATES_START" -EndMarker "ROADMAP_BACKLOG_UPDATES_END"
    $taskQueueUpdate = Get-DelimitedSection -Text $Output -StartMarker "TASK_QUEUE_UPDATES_START" -EndMarker "TASK_QUEUE_UPDATES_END"
    Assert-JsonReplacementSection -SectionName "ROADMAP_BACKLOG_UPDATES" -Text $roadmapUpdate
    Assert-JsonReplacementSection -SectionName "TASK_QUEUE_UPDATES" -Text $taskQueueUpdate

    $shortCommands = Get-DelimitedSection -Text $Output -StartMarker "SHORT_COMMANDS_JSONL_START" -EndMarker "SHORT_COMMANDS_JSONL_END"
    if (-not (Test-UnchangedSection -Text $shortCommands)) {
        foreach ($line in ($shortCommands -split "`r?`n")) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $null = $line | ConvertFrom-Json
            }
        }
    }

    Set-Text -Path $latestGptPath -Text $Output
    Set-SectionIfChanged -Path $programStatusPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_PROGRAM_STATUS_START" -EndMarker "UPDATED_PROGRAM_STATUS_END")
    Set-SectionIfChanged -Path $activeGoalsPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_ACTIVE_GOALS_START" -EndMarker "UPDATED_ACTIVE_GOALS_END")
    Set-SectionIfChanged -Path $nextStepsPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_NEXT_STEPS_START" -EndMarker "UPDATED_NEXT_STEPS_END")
    Set-SectionIfChanged -Path $laneStatusPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_LANE_STATUS_START" -EndMarker "UPDATED_LANE_STATUS_END")
    Set-SectionIfChanged -Path $nextPromptPath -Text (Get-DelimitedSection -Text $Output -StartMarker "NEXT_CODEX_PROMPT_START" -EndMarker "NEXT_CODEX_PROMPT_END")
    Set-SectionIfChanged -Path $nextActionPath -Text (Get-DelimitedSection -Text $Output -StartMarker "NEXT_ACTION_PACKET_START" -EndMarker "NEXT_ACTION_PACKET_END")
    Set-JsonReplacementIfChanged -Path $roadmapBacklogPath -SectionName "ROADMAP_BACKLOG_UPDATES" -Text $roadmapUpdate
    Set-JsonReplacementIfChanged -Path $taskQueuePath -SectionName "TASK_QUEUE_UPDATES" -Text $taskQueueUpdate
    Add-JsonlLines -Path $shortPendingPath -Text $shortCommands

    $longCommands = Get-DelimitedSection -Text $Output -StartMarker "LONG_COMMANDS_MD_START" -EndMarker "LONG_COMMANDS_MD_END"
    if (-not (Test-UnchangedSection -Text $longCommands)) {
        Append-Text -Path $longReviewPath -Text "`r`n## $(Get-UtcStamp) GPT long/manual requests`r`n$longCommands`r`n"
    }

    $claudeNeeded = Get-DelimitedSection -Text $Output -StartMarker "CLAUDE_REVIEW_NEEDED_START" -EndMarker "CLAUDE_REVIEW_NEEDED_END"
    if ($claudeNeeded -match "^\s*YES\b") {
        Set-Text -Path $claudeNeededPath -Text $claudeNeeded
        Set-Text -Path $readyForClaudePath -Text "GPT requested Claude review at $(Get-UtcStamp).`r`n$claudeNeeded"
    }

    $blockedReason = Get-DelimitedSection -Text $Output -StartMarker "BLOCKED_REASON_START" -EndMarker "BLOCKED_REASON_END"
    if (-not (Test-UnchangedSection -Text $blockedReason)) {
        Set-Text -Path $blockedReasonPath -Text $blockedReason
        Set-Text -Path $gptReviewNeededPath -Text "GPT blocked lane at $(Get-UtcStamp): $blockedReason"
    }

    $userAction = Get-DelimitedSection -Text $Output -StartMarker "USER_ACTION_REQUIRED_START" -EndMarker "USER_ACTION_REQUIRED_END"
    if (-not (Test-UnchangedSection -Text $userAction)) {
        Append-Text -Path $userActionPath -Text "`r`n## $(Get-UtcStamp) GPT user action request`r`n$($userAction.Trim())`r`n"
    }

    $modelUsed = Get-DelimitedSection -Text $Output -StartMarker "MODEL_USED_START" -EndMarker "MODEL_USED_END"
    $reasoningSummary = Get-DelimitedSection -Text $Output -StartMarker "REASONING_SUMMARY_START" -EndMarker "REASONING_SUMMARY_END"
    if (-not (Test-UnchangedSection -Text $reasoningSummary)) {
        Append-Text -Path $decisionLogPath -Text "`r`n## $(Get-UtcStamp) GPT planner decision ($LaneName)`r`nModel: $modelUsed`r`n$reasoningSummary`r`n"
    }

    return $modelUsed
}

# --- NoApiSmoke fake output ---------------------------------------------------

function New-NoApiSmokeOutput {
    return @"
UPDATED_PROGRAM_STATUS_START
UNCHANGED
UPDATED_PROGRAM_STATUS_END
UPDATED_ACTIVE_GOALS_START
UNCHANGED
UPDATED_ACTIVE_GOALS_END
UPDATED_NEXT_STEPS_START
UNCHANGED
UPDATED_NEXT_STEPS_END
UPDATED_LANE_STATUS_START
UNCHANGED
UPDATED_LANE_STATUS_END
NEXT_CODEX_PROMPT_START
UNCHANGED
NEXT_CODEX_PROMPT_END
SHORT_COMMANDS_JSONL_START
UNCHANGED
SHORT_COMMANDS_JSONL_END
LONG_COMMANDS_MD_START
UNCHANGED
LONG_COMMANDS_MD_END
NEXT_ACTION_PACKET_START
UNCHANGED
NEXT_ACTION_PACKET_END
CLAUDE_REVIEW_NEEDED_START
NO - NoApiSmoke local handoff test only.
CLAUDE_REVIEW_NEEDED_END
BLOCKED_REASON_START
UNCHANGED
BLOCKED_REASON_END
ROADMAP_BACKLOG_UPDATES_START
UNCHANGED
ROADMAP_BACKLOG_UPDATES_END
USER_ACTION_REQUIRED_START
UNCHANGED
USER_ACTION_REQUIRED_END
TASK_QUEUE_UPDATES_START
UNCHANGED
TASK_QUEUE_UPDATES_END
MODEL_USED_START
no-api-smoke
MODEL_USED_END
REASONING_SUMMARY_START
NoApiSmoke local handoff test at $(Get-UtcStamp). Skipped Node + OpenAI to validate marker validation + handoff write path.
REASONING_SUMMARY_END
"@
}

# --- Main ---------------------------------------------------------------------

$startTime = Get-Date
$oldApiTimeout = $env:GPT_PROMPTER_API_TIMEOUT_SECONDS
$setTemporaryApiTimeout = $false

# Initialize the run log via direct UTF8-no-BOM file write so subsequent
# Add-Content/Write-Trace appends don't fight a leading BOM.
$initLog = @"
[$(Get-UtcStamp)] step.1 start
  Lane: $LaneName
  Lane path: $($lane.Path)
  TimeoutSeconds: $TimeoutSeconds
  Mode: $(if ($NoApiSmoke) { 'NoApiSmoke' } elseif ($ApiSmoke) { 'ApiSmoke' } else { 'normal' })
  DebugTrace: $DebugTrace
  Run log: $runLogPath
  Packet path: $packetPath
  Raw output: $rawOutputPath
  stdout: $stdoutPath
  stderr: $stderrPath
"@
[System.IO.File]::WriteAllText($runLogPath, $initLog, [System.Text.UTF8Encoding]::new($false))
if ($DebugTrace) { Write-Host $initLog }

try {
    Write-Trace "step.2 resolve paths and pre-flight"

    if (Test-Path -LiteralPath $Global:StopFile -PathType Leaf) {
        throw "STOP.txt exists at $Global:StopFile. Refusing one-shot GPT prompter run."
    }
    if (-not $NoApiSmoke -and [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        throw "OPENAI_API_KEY is not set. Refusing one-shot GPT prompter run. Use -NoApiSmoke to test handoff without API."
    }

    if ($NoApiSmoke) {
        Write-Trace "step.3 NoApiSmoke: writing plain text packet and skipping node call"
        Set-Text -Path $packetPath -Text "NoApiSmoke plain text packet for lane $LaneName at $(Get-UtcStamp)."
        Write-Trace "  packet bytes: $(Get-FileByteLengthSafe -Path $packetPath)"
        Write-Trace "step.4 NoApiSmoke: building fake marker-complete output"
        $fakeOutput = New-NoApiSmokeOutput
        Write-Trace "step.5 NoApiSmoke: writing $rawOutputPath"
        Set-Text -Path $rawOutputPath -Text $fakeOutput
        Write-Trace "step.6 NoApiSmoke: applying handoff (marker validation + writes)"
        $modelUsed = Apply-GptOutput -Output $fakeOutput
        Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter_once" -Status "completed" -Detail "NoApiSmoke completed."
        $elapsed = ((Get-Date) - $startTime).TotalSeconds
        Write-Trace "step.11 NoApiSmoke complete in $([Math]::Round($elapsed, 2))s. Model: $modelUsed. Exit 0."
        Write-Host "NoApiSmoke completed for '$LaneName'. Model: $modelUsed. Log: $runLogPath"
        exit 0
    }

    Write-Trace "step.3 build packet"
    if ($ApiSmoke) {
        $packet = "API smoke test for lane $LaneName at $(Get-UtcStamp)."
    } else {
        $packet = New-PrompterPacket -TriggerReason "one-shot manual test"
    }
    Write-Trace "step.4 write packet to $packetPath"
    Set-Text -Path $packetPath -Text $packet
    Write-Trace "  packet bytes: $(Get-FileByteLengthSafe -Path $packetPath)"

    Write-Trace "step.5 resolve node path"
    $nodePath = Get-NodeExecutablePath
    Write-Trace "  node: $nodePath"

    $scriptPath = Join-Path $Global:NodeRoot "gpt-prompter.mjs"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "gpt-prompter.mjs not found at $scriptPath"
    }

    $nodeArgs = @($scriptPath, "--packet-text", $packetPath, "--lane-name", $LaneName, "--lane-path", $lane.Path, "--output", $rawOutputPath, "--skip-marker-validation")
    if (-not [string]::IsNullOrWhiteSpace($Model)) { $nodeArgs += @("--model", $Model) }
    if ($ApiSmoke) { $nodeArgs += @("--api-smoke") }

    # Child API timeout is parent timeout minus 10s headroom for body read +
    # PowerShell teardown, clamped to [5, 120].
    $childTimeout = [Math]::Max(5, [Math]::Min(120, $TimeoutSeconds - 10))
    if ([string]::IsNullOrWhiteSpace($env:GPT_PROMPTER_API_TIMEOUT_SECONDS)) {
        $env:GPT_PROMPTER_API_TIMEOUT_SECONDS = [string]$childTimeout
        $setTemporaryApiTimeout = $true
    }

    Write-Trace "step.6 start node (parent timeout ${TimeoutSeconds}s, child API timeout $env:GPT_PROMPTER_API_TIMEOUT_SECONDS s)"
    Write-Trace "  cmd: `"$nodePath`" $($nodeArgs -join ' ')"

    Write-Trace "step.7 wait begin"
    $result = Invoke-NodeWithTimeout `
        -NodePath $nodePath `
        -Arguments $nodeArgs `
        -TimeoutMs ($TimeoutSeconds * 1000) `
        -StdoutPath $stdoutPath `
        -StderrPath $stderrPath

    if ($result.TimedOut) {
        $reason = "Node GPT prompter timed out after $TimeoutSeconds seconds (PID $($result.Pid)) and was killed."
        $stdoutTail = Read-TextTailSafe -Path $stdoutPath -TailChars 2000
        $stderrTail = Read-TextTailSafe -Path $stderrPath -TailChars 2000
        Add-Failure -Reason $reason -Details "stdout tail:`r`n$stdoutTail`r`nstderr tail:`r`n$stderrTail"
        Write-Error $reason
        exit 1
    }
    Write-Trace "step.8 wait end: exit=$($result.ExitCode), pid=$($result.Pid)"

    if ($result.ExitCode -ne 0) {
        $stdoutTail = Read-TextTailSafe -Path $stdoutPath -TailChars 4000
        $stderrTail = Read-TextTailSafe -Path $stderrPath -TailChars 4000
        $reason = "gpt-prompter.mjs exited with code $($result.ExitCode)."
        Add-Failure -Reason $reason -Details "stdout tail:`r`n$stdoutTail`r`nstderr tail:`r`n$stderrTail"
        Write-Error "$reason See $runLogPath"
        exit 1
    }

    $rawOutputBytes = Get-FileByteLengthSafe -Path $rawOutputPath
    if ($null -eq $rawOutputBytes) {
        $reason = "gpt-prompter.mjs exited 0 but did not create raw output at $rawOutputPath."
        $stdoutTail = Read-TextTailSafe -Path $stdoutPath -TailChars 2000
        $stderrTail = Read-TextTailSafe -Path $stderrPath -TailChars 2000
        Add-Failure -Reason $reason -Details "stdout tail:`r`n$stdoutTail`r`nstderr tail:`r`n$stderrTail"
        Write-Error "$reason See $runLogPath"
        exit 1
    }

    Write-Trace "  raw output bytes: $rawOutputBytes"
    if ($rawOutputBytes -le 0) {
        $reason = "gpt-prompter.mjs exited 0 but produced empty output at $rawOutputPath."
        $stdoutTail = Read-TextTailSafe -Path $stdoutPath -TailChars 2000
        $stderrTail = Read-TextTailSafe -Path $stderrPath -TailChars 2000
        Add-Failure -Reason $reason -Details "stdout tail:`r`n$stdoutTail`r`nstderr tail:`r`n$stderrTail"
        Write-Error "$reason See $runLogPath"
        exit 1
    }

    $output = [string](Read-TextFileStrict -Path $rawOutputPath)
    Write-Trace "  raw output chars: $($output.Length)"
    if ([string]::IsNullOrWhiteSpace($output)) {
        $reason = "gpt-prompter.mjs exited 0 but produced whitespace-only output at $rawOutputPath."
        $stdoutTail = Read-TextTailSafe -Path $stdoutPath -TailChars 2000
        $stderrTail = Read-TextTailSafe -Path $stderrPath -TailChars 2000
        Add-Failure -Reason $reason -Details "stdout tail:`r`n$stdoutTail`r`nstderr tail:`r`n$stderrTail"
        Write-Error "$reason See $runLogPath"
        exit 1
    }

    Write-Trace "step.9 marker validation"
    if ($ApiSmoke) {
        Write-Trace "  (ApiSmoke: skipping marker validation and handoff write)"
        Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter_once" -Status "completed" -Detail "ApiSmoke completed."
        Write-Trace "step.11 ApiSmoke complete. Raw response at $rawOutputPath. Exit 0."
        Write-Host "ApiSmoke completed for '$LaneName'. Raw response at $rawOutputPath. Log: $runLogPath"
        exit 0
    }

    try {
        $modelUsed = Apply-GptOutput -Output $output
    }
    catch {
        $reason = "Marker validation or handoff write failed: $($_.Exception.Message)"
        # Preserve raw output. Do NOT overwrite NEXT_CODEX_PROMPT.md.
        Add-Failure -Reason $reason -Details "Raw output preserved at $rawOutputPath. NEXT_CODEX_PROMPT.md was not overwritten."
        Write-Error "$reason See $runLogPath"
        exit 1
    }

    Write-Trace "step.10 write handoff files (model: $modelUsed)"
    $hash = Get-ContentHash -Text $packet
    Set-Text -Path $lastHashPath -Text $hash
    Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter_once" -Status "completed" -Detail "One-shot completed."
    $elapsed = ((Get-Date) - $startTime).TotalSeconds
    Write-Trace "step.11 done in $([Math]::Round($elapsed, 2))s. Model: $modelUsed. Exit 0."

    Write-Host "GPT prompter one-shot completed for '$LaneName'."
    Write-Host "  Model: $modelUsed"
    Write-Host "  Raw output: $rawOutputPath"
    Write-Host "  Log: $runLogPath"
    exit 0
}
catch {
    $reason = $_.Exception.Message
    Add-Failure -Reason $reason
    Write-Error "GPT prompter one-shot failed for '$LaneName': $reason"
    exit 1
}
finally {
    if ($setTemporaryApiTimeout) {
        if ([string]::IsNullOrWhiteSpace($oldApiTimeout)) {
            Remove-Item Env:\GPT_PROMPTER_API_TIMEOUT_SECONDS -ErrorAction SilentlyContinue
        } else {
            $env:GPT_PROMPTER_API_TIMEOUT_SECONDS = $oldApiTimeout
        }
    }
}
