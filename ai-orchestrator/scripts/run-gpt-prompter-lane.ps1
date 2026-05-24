param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName,

    [switch]$Once,

    [ValidateRange(1, 3600)]
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

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
$gptReviewNeededPath = Join-Path $loopPath "GPT_REVIEW_NEEDED.txt"
$reviewMarkerPath = Join-Path $loopPath "READY_FOR_CLAUDE_REVIEW.txt"
$failureLogPath = Join-Path $loopPath "FAILURE_LOG.md"
$lastHashPath = Join-Path $loopPath "LAST_GPT_INPUT_HASH.txt"

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

function Add-JsonlLines {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [AllowEmptyString()][string]$Text
    )

    if (Test-UnchangedSection -Text $Text) { return }
    foreach ($line in ($Text -split "`r?`n")) {
        if (-not [string]::IsNullOrWhiteSpace($line)) {
            Add-Content -LiteralPath $Path -Value $line.Trim() -Encoding UTF8
        }
    }
}

function Test-ActivePrompt {
    param([AllowEmptyString()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    if ($Text -match "(?i)No active prompt assigned") { return $false }
    return ($Text -match "(?i)\b(Task ID|TaskId|task_id|Selected task)\b")
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
    try {
        $null = $Text | ConvertFrom-Json
    }
    catch {
        throw "$SectionName did not contain valid JSON replacement text: $($_.Exception.Message)"
    }
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

function Append-UserActionIfChanged {
    param([AllowEmptyString()][string]$Text)

    if (Test-UnchangedSection -Text $Text) { return }
    Append-Text -Path $userActionPath -Text "`r`n## $(Get-UtcStamp) GPT user action request`r`n$($Text.Trim())`r`n"
}

function New-PrompterPacket {
    param([Parameter(Mandatory = $true)][string]$TriggerReason)

    $git = Get-GitSnapshot -Lane $lane
    $riskSensitive = Test-HasRiskSensitiveChange -DiffNames $git.DiffNames
    $laneStatus = Read-TextIfExists -Path $laneStatusPath
    $blocked = $laneStatus -match "Status:\s*BLOCKED"
    $commandTail = Read-TextIfExists -Path $commandResultsPath -TailChars $Global:CommandResultsTailChars
    $testsFailed = $commandTail -match "(?i)\bfailed\b|exit_code:\s*[1-9]"
    $includeFullDiff = $riskSensitive -or $blocked -or $testsFailed
    $fullDiff = "(full diff intentionally omitted by default)"
    if ($includeFullDiff) {
        $fullDiff = Invoke-GitText -Cwd $lane.Path -Args @("diff", "--no-ext-diff")
        if ($fullDiff.Length -gt $Global:ReviewDiffTailChars) {
            $fullDiff = $fullDiff.Substring(0, $Global:ReviewDiffTailChars) + "`r`n(diff truncated)"
        }
    }

    $failureLog = Read-TextIfExists -Path $failureLogPath -TailChars $Global:FailureLogTailChars
    $failureCount = ([regex]::Matches($failureLog, "Codex failure")).Count
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

    return [ordered]@{
        laneName = $LaneName
        lanePath = $lane.Path
        triggerReason = $TriggerReason
        gptCheapModel = $Global:GptCheapModel
        gptDefaultModel = $Global:GptDefaultModel
        gptStrategicModel = $Global:GptStrategicModel
        laneStatus = $laneStatus
        failureLogTail = $failureLog
        failureCount = $failureCount
        taskQueue = $taskQueue
        reviewPolicy = $reviewPolicy
        roadmapBacklog = $roadmapBacklog
        blockerAnalysis = $blockerAnalysis
        venueExpansionPlan = $venueExpansion
        gitStatus = $git.Status
        gitDiffStat = $git.DiffStat
        gitDiffNames = $git.DiffNames
        riskSensitiveChange = $riskSensitive
        testsFailed = $testsFailed
        includeFullDiff = $includeFullDiff
        latestCodexSummary = Read-TextIfExists -Path $latestCodexPath
        latestClaudeReview = Read-TextIfExists -Path $latestClaudePath
        text = $text
    }
}

function Join-ProcessArguments {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $escaped = foreach ($arg in $Arguments) {
        if ($arg -match '[\s"]') {
            '"' + ($arg -replace '"', '\"') + '"'
        }
        else {
            $arg
        }
    }
    return ($escaped -join " ")
}

function Invoke-GptPrompterNode {
    param(
        [Parameter(Mandatory = $true)][string]$PacketPath,
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [int]$TimeoutSeconds = 0,
        [string]$RunLogPath = ""
    )

    $nodePath = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (-not (Test-Path -LiteralPath $nodePath -PathType Leaf)) {
        $node = Get-Command node -ErrorAction SilentlyContinue
        if ($node) { $nodePath = $node.Source }
    }
    if (-not (Test-Path -LiteralPath $nodePath -PathType Leaf)) {
        throw "node executable not found."
    }

    $script = Join-Path $Global:NodeRoot "gpt-prompter.mjs"
    if ($TimeoutSeconds -gt 0) {
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = $nodePath
        $startInfo.Arguments = Join-ProcessArguments -Arguments @($script, "--input", $PacketPath, "--output", $OutputPath)
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        if ([string]::IsNullOrWhiteSpace($startInfo.EnvironmentVariables["GPT_PROMPTER_API_TIMEOUT_SECONDS"])) {
            $startInfo.EnvironmentVariables["GPT_PROMPTER_API_TIMEOUT_SECONDS"] = [string]([Math]::Min(120, [Math]::Max(1, $TimeoutSeconds - 15)))
        }

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo

        if (-not [string]::IsNullOrWhiteSpace($RunLogPath)) {
            Write-PrompterRunLog -Path $RunLogPath -Text "[$(Get-UtcStamp)] Node timeout: $TimeoutSeconds seconds."
            Write-PrompterRunLog -Path $RunLogPath -Text "[$(Get-UtcStamp)] Node API timeout env: $($startInfo.EnvironmentVariables["GPT_PROMPTER_API_TIMEOUT_SECONDS"]) seconds."
        }

        [void]$process.Start()
        $finished = $process.WaitForExit($TimeoutSeconds * 1000)
        if (-not $finished) {
            try {
                $process.Kill()
                $process.WaitForExit(5000) | Out-Null
            }
            catch {
                if (-not [string]::IsNullOrWhiteSpace($RunLogPath)) {
                    Write-PrompterRunLog -Path $RunLogPath -Text "[$(Get-UtcStamp)] Failed to kill timed-out node process: $($_.Exception.Message)"
                }
            }
            return [pscustomobject]@{
                ExitCode = -1
                Output = "Node GPT prompter timed out after $TimeoutSeconds seconds and was killed."
                TimedOut = $true
            }
        }

        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $combinedOutput = (($stdout, $stderr) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join "`r`n"

        return [pscustomobject]@{
            ExitCode = [int]$process.ExitCode
            Output = $combinedOutput
            TimedOut = $false
        }
    }

    $output = & $nodePath $script --input $PacketPath --output $OutputPath 2>&1
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = ($output -join "`r`n")
        TimedOut = $false
    }
}

function Write-PrompterRunLog {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    Add-Content -LiteralPath $Path -Value $Text -Encoding UTF8
}

function Apply-GptPrompterOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Output,
        [Parameter(Mandatory = $true)][string]$RunLogPath
    )

    Set-Text -Path $latestGptPath -Text $Output
    Assert-RequiredMarkers -Text $Output
    Write-PrompterRunLog -Path $RunLogPath -Text "[$(Get-UtcStamp)] Marker validation: passed."

    $roadmapUpdate = Get-DelimitedSection -Text $Output -StartMarker "ROADMAP_BACKLOG_UPDATES_START" -EndMarker "ROADMAP_BACKLOG_UPDATES_END"
    $taskQueueUpdate = Get-DelimitedSection -Text $Output -StartMarker "TASK_QUEUE_UPDATES_START" -EndMarker "TASK_QUEUE_UPDATES_END"
    Assert-JsonReplacementSection -SectionName "ROADMAP_BACKLOG_UPDATES" -Text $roadmapUpdate
    Assert-JsonReplacementSection -SectionName "TASK_QUEUE_UPDATES" -Text $taskQueueUpdate

    Set-SectionIfChanged -Path $programStatusPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_PROGRAM_STATUS_START" -EndMarker "UPDATED_PROGRAM_STATUS_END")
    Set-SectionIfChanged -Path $activeGoalsPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_ACTIVE_GOALS_START" -EndMarker "UPDATED_ACTIVE_GOALS_END")
    Set-SectionIfChanged -Path $nextStepsPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_NEXT_STEPS_START" -EndMarker "UPDATED_NEXT_STEPS_END")
    Set-SectionIfChanged -Path $laneStatusPath -Text (Get-DelimitedSection -Text $Output -StartMarker "UPDATED_LANE_STATUS_START" -EndMarker "UPDATED_LANE_STATUS_END")
    Set-SectionIfChanged -Path $nextPromptPath -Text (Get-DelimitedSection -Text $Output -StartMarker "NEXT_CODEX_PROMPT_START" -EndMarker "NEXT_CODEX_PROMPT_END")
    Set-SectionIfChanged -Path $nextActionPath -Text (Get-DelimitedSection -Text $Output -StartMarker "NEXT_ACTION_PACKET_START" -EndMarker "NEXT_ACTION_PACKET_END")
    Set-JsonReplacementIfChanged -Path $roadmapBacklogPath -SectionName "ROADMAP_BACKLOG_UPDATES" -Text $roadmapUpdate
    Set-JsonReplacementIfChanged -Path $taskQueuePath -SectionName "TASK_QUEUE_UPDATES" -Text $taskQueueUpdate

    Add-JsonlLines -Path $shortPendingPath -Text (Get-DelimitedSection -Text $Output -StartMarker "SHORT_COMMANDS_JSONL_START" -EndMarker "SHORT_COMMANDS_JSONL_END")

    $longCommands = Get-DelimitedSection -Text $Output -StartMarker "LONG_COMMANDS_MD_START" -EndMarker "LONG_COMMANDS_MD_END"
    if (-not (Test-UnchangedSection -Text $longCommands)) {
        Append-Text -Path $longReviewPath -Text "`r`n## $(Get-UtcStamp) GPT long/manual requests`r`n$longCommands`r`n"
    }

    $claudeNeeded = Get-DelimitedSection -Text $Output -StartMarker "CLAUDE_REVIEW_NEEDED_START" -EndMarker "CLAUDE_REVIEW_NEEDED_END"
    if ($claudeNeeded -match "^\s*YES\b") {
        Set-Text -Path $reviewMarkerPath -Text "GPT requested Claude review at $(Get-UtcStamp).`r`n$claudeNeeded"
    }

    $blockedReason = Get-DelimitedSection -Text $Output -StartMarker "BLOCKED_REASON_START" -EndMarker "BLOCKED_REASON_END"
    if (-not (Test-UnchangedSection -Text $blockedReason)) {
        Append-Text -Path $failureLogPath -Text "`r`n## $(Get-UtcStamp) GPT blocked lane`r`n$blockedReason`r`n"
        Set-Text -Path $gptReviewNeededPath -Text "GPT blocked lane at $(Get-UtcStamp): $blockedReason"
    }

    Append-UserActionIfChanged -Text (Get-DelimitedSection -Text $Output -StartMarker "USER_ACTION_REQUIRED_START" -EndMarker "USER_ACTION_REQUIRED_END")

    $modelUsed = Get-DelimitedSection -Text $Output -StartMarker "MODEL_USED_START" -EndMarker "MODEL_USED_END"
    $reasoningSummary = Get-DelimitedSection -Text $Output -StartMarker "REASONING_SUMMARY_START" -EndMarker "REASONING_SUMMARY_END"
    Write-PrompterRunLog -Path $RunLogPath -Text "[$(Get-UtcStamp)] Model used: $modelUsed"
    if (-not (Test-UnchangedSection -Text $reasoningSummary)) {
        Append-Text -Path $decisionLogPath -Text "`r`n## $(Get-UtcStamp) GPT planner decision ($LaneName)`r`nModel: $modelUsed`r`n$reasoningSummary`r`n"
    }

    if (Test-Path -LiteralPath $gptReviewNeededPath -PathType Leaf -and (Test-UnchangedSection -Text $blockedReason)) {
        Remove-Item -LiteralPath $gptReviewNeededPath -Force
    }

    return [pscustomobject]@{
        ModelUsed = $modelUsed
        ClaudeNeeded = $claudeNeeded
        BlockedReason = $blockedReason
    }
}

function Invoke-GptPrompterCycle {
    param(
        [Parameter(Mandatory = $true)][string]$TriggerReason,
        [switch]$IsOnce,
        [int]$NodeTimeoutSeconds = 0
    )

    if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        throw "OPENAI_API_KEY is not set."
    }

    $packetPreview = New-PrompterPacket -TriggerReason $TriggerReason
    $hash = Get-ContentHash -Text ($packetPreview | ConvertTo-Json -Depth 8)
    $runCount = Get-AndIncrementRunCounter -Lane $lane -CounterName "gpt_prompter"
    $stamp = Get-FileSafeStamp
    $packetPath = Join-Path $logRoot "gpt_packet_${stamp}_run${runCount}.json"
    $outputPath = Join-Path $logRoot "gpt_prompter_${stamp}_run${runCount}.md"
    $runLogPath = Join-Path $logRoot "gpt_prompter_${stamp}_run${runCount}.log"

    Set-Text -Path $packetPath -Text ($packetPreview | ConvertTo-Json -Depth 10)
    Set-Text -Path $runLogPath -Text @"
[$(Get-UtcStamp)] GPT prompter cycle started.
Lane: $LaneName
Mode: $(if ($IsOnce) { "once" } else { "loop" })
Packet: $packetPath
Output: $outputPath
"@

    Write-Host "[$(Get-UtcStamp)] Calling GPT prompter for '$LaneName'. Packet: $packetPath"
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] API call start."
    Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter" -Status "running" -Detail "Run $runCount"

    $nodeResult = Invoke-GptPrompterNode -PacketPath $packetPath -OutputPath $outputPath -TimeoutSeconds $NodeTimeoutSeconds -RunLogPath $runLogPath
    if ($nodeResult.TimedOut) {
        Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] API call timeout/failure: $($nodeResult.Output)"
        throw $nodeResult.Output
    }
    if ($nodeResult.ExitCode -ne 0) {
        Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] API call failed: $($nodeResult.Output)"
        throw "gpt-prompter.mjs exited with code $($nodeResult.ExitCode): $($nodeResult.Output)"
    }
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] API call end."

    $output = Read-TextIfExists -Path $outputPath
    $applied = Apply-GptPrompterOutput -Output $output -RunLogPath $runLogPath

    Set-Text -Path $lastHashPath -Text $hash
    Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter" -Status "sleeping" -Detail "Completed run $runCount"
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] Output files written."
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] Latest GPT: $latestGptPath"
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] Next prompt: $nextPromptPath"
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] Next action: $nextActionPath"
    Write-PrompterRunLog -Path $runLogPath -Text "[$(Get-UtcStamp)] GPT prompter cycle completed."

    return [pscustomobject]@{
        Hash = $hash
        RunCount = $runCount
        PacketPath = $packetPath
        OutputPath = $outputPath
        RunLogPath = $runLogPath
        ModelUsed = $applied.ModelUsed
    }
}

$lastHash = (Read-TextIfExists -Path $lastHashPath).Trim()
$warnedNoKey = $false

if ($Once) {
    Write-Warning "-Once on run-gpt-prompter-lane.ps1 is deprecated. Delegating to run-gpt-prompter-once.ps1."
    & (Join-Path $PSScriptRoot "run-gpt-prompter-once.ps1") -LaneName $LaneName -TimeoutSeconds $TimeoutSeconds
    exit $LASTEXITCODE
}

Write-Host "GPT prompter supervisor loop started for '$LaneName'. Stop file: $Global:StopFile"

while (-not (Test-Path -LiteralPath $Global:StopFile -PathType Leaf)) {
    Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter" -Status "waiting" -Detail "Polling context."

    $triggerReasons = @()
    if (Test-Path -LiteralPath $gptReviewNeededPath -PathType Leaf) { $triggerReasons += "GPT_REVIEW_NEEDED.txt exists" }
    if ((Read-TextIfExists -Path $laneStatusPath) -match "Status:\s*BLOCKED") { $triggerReasons += "lane is BLOCKED" }

    $triggerReason = (($triggerReasons + @("content poll")) -join "; ")
    $packetPreview = New-PrompterPacket -TriggerReason $triggerReason
    $hash = Get-ContentHash -Text ($packetPreview | ConvertTo-Json -Depth 8)

    $currentPrompt = Read-TextIfExists -Path $nextPromptPath
    $activePromptWaiting = Test-ActivePrompt -Text $currentPrompt

    if ($hash -eq $lastHash -and $triggerReasons.Count -eq 0) {
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    if ($activePromptWaiting -and $triggerReasons.Count -eq 0 -and [string]::IsNullOrWhiteSpace($lastHash)) {
        Set-Text -Path $lastHashPath -Text $hash
        $lastHash = $hash
        Write-Host "[$(Get-UtcStamp)] Active prompt already exists for '$LaneName'; waiting for new summary/result/review before GPT rewrite."
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
        if (-not $warnedNoKey) {
            Write-Warning "OPENAI_API_KEY is not set. GPT prompter for '$LaneName' will wait without calling the API."
            $warnedNoKey = $true
        }
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    try {
        $result = Invoke-GptPrompterCycle -TriggerReason $triggerReason
        $lastHash = $result.Hash
    }
    catch {
        $message = "[$(Get-UtcStamp)] GPT prompter failed for '$LaneName': $($_.Exception.Message)"
        Write-Warning $message
        Append-Text -Path $latestGptPath -Text "`r`n$message`r`n"
        Append-Text -Path $failureLogPath -Text "`r`n## $(Get-UtcStamp) GPT prompter failure`r`n$message`r`n"
        Set-Text -Path $gptReviewNeededPath -Text "GPT prompter failure at $(Get-UtcStamp). Do not overwrite NEXT_CODEX_PROMPT.md until marker/schema issue is fixed.`r`n$message"
        Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter" -Status "failed" -Detail $_.Exception.Message
    }

    Start-Sleep -Seconds $Global:PromptPollSeconds
}

Set-LaneHeartbeat -Lane $lane -Role "gpt_prompter" -Status "stopped" -Detail "STOP file detected."
Write-Host "GPT prompter supervisor loop stopped for '$LaneName'."
