param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane
$logRoot = Join-Path (Get-LaneLogRoot -LaneName $LaneName) "codex"
Ensure-Directory -Path $logRoot

$promptPath = Join-Path $loopPath "NEXT_CODEX_PROMPT.md"
$summaryPath = Join-Path $loopPath "LATEST_CODEX_SUMMARY.md"
$laneStatusPath = Join-Path $loopPath "LANE_STATUS.md"
$failureLogPath = Join-Path $loopPath "FAILURE_LOG.md"
$reviewMarkerPath = Join-Path $loopPath "READY_FOR_CLAUDE_REVIEW.txt"
$gptReviewNeededPath = Join-Path $loopPath "GPT_REVIEW_NEEDED.txt"
$recoveryPacketPath = Join-Path $loopPath "RECOVERY_CONTEXT_PACKET.md"

function Get-FileWriteUtc {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Get-Item -LiteralPath $Path).LastWriteTimeUtc
    }
    return [datetime]::MinValue
}

function Test-CodexFailureText {
    param([Parameter(Mandatory = $true)][string]$Text)

    $patterns = @(
        "compacting context",
        "Remote compact task",
        "stream disconnected",
        "context length",
        "context_length_exceeded",
        "rate limit",
        "ECONNRESET",
        "ETIMEDOUT",
        "\bnetwork\b"
    )

    foreach ($pattern in $patterns) {
        if ($Text -match $pattern) { return $true }
    }
    return $false
}

function New-CodexWorkerPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$LanePrompt,
        [string]$RecoveryPacket = ""
    )

    $recoverySection = ""
    if (-not [string]::IsNullOrWhiteSpace($RecoveryPacket)) {
        $recoverySection = @"

RECOVERY CONTEXT:
$RecoveryPacket
"@
    }

    return @"
You are the Codex lane worker for lane '$LaneName'.
Lane repo path: $($lane.Path)

Do exactly one small, bounded task from the lane prompt below.

Operating rules:
- Work only inside this lane repo and its `.ai_loop` handoff files.
- Do not commit or push.
- Do not add live trading, order submission, auth/account/private-key/signing/wallet/deploy/git push logic.
- Do not read, print, move, modify, or commit secrets.
- Do not auto-run long/risky commands.
- If you need a safe short diagnostic command, append a JSON line to `.ai_loop/COMMANDS_SHORT_PENDING.jsonl`.
- If you need a long, risky, manual, network-heavy, or ambiguous command, append it to `.ai_loop/COMMANDS_LONG_REVIEW.md`.
- Do not bury command requests only in prose.
- At the end, update `.ai_loop/LATEST_CODEX_SUMMARY.md` with a compact summary of what changed, what was verified, and what remains.
- At the end, update `.ai_loop/LANE_STATUS.md` when lane state, blocker, or priority changes.
- At the end, update `.ai_loop/NEXT_CODEX_PROMPT.md` with the exact next small Codex task.
- Keep uncertain outputs as WATCH or MANUAL_REVIEW.

Safe short command JSONL schema:
{"id":"unique-stable-id","classification":"SAFE_SHORT_AUTO","cwd":"$($lane.Path)","command":"git status --short","reason":"why this is needed","expected_output":"what success looks like","timeout_seconds":30}

Your LATEST_CODEX_SUMMARY.md must include:
- task id
- files changed
- commands run
- tests run
- pass/fail
- paper candidates count
- risk flags
- whether Claude review is needed
- recommended next task
- whether docs/state were updated

Guardrails:
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "GLOBAL_GUARDRAILS.md"))

Lane stable context:
$(Read-TextIfExists -Path (Join-Path $loopPath "LANE_CONTEXT.md"))

Program state:
$(Read-TextIfExists -Path (Join-Path $Global:StateRoot "PROGRAM_STATUS.md"))

Active goals:
$(Read-TextIfExists -Path (Join-Path $Global:StateRoot "ACTIVE_GOALS.md"))

Lane prompt starts below.

$LanePrompt
$recoverySection
"@
}

function Invoke-CodexOnce {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$LogPath
    )

    $job = Start-Job -ScriptBlock {
        param($JobCwd, $JobPrompt)
        Set-Location -LiteralPath $JobCwd
        $jobOutput = & codex exec --sandbox workspace-write $JobPrompt 2>&1 | ForEach-Object { [string]$_ }
        [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output = ($jobOutput -join "`r`n")
        }
    } -ArgumentList $lane.Path, $Prompt

    $finished = Wait-Job -Job $job -Timeout $Global:CodexTimeoutSeconds
    if (-not $finished) {
        Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        $text = "Codex timed out after $Global:CodexTimeoutSeconds seconds."
        Set-Text -Path $LogPath -Text $text
        Write-Warning $text
        return [pscustomobject]@{ ExitCode = -1; Output = $text; TimedOut = $true }
    }

    $received = @(Receive-Job -Job $job)
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    if ($received.Count -eq 0) {
        $result = [pscustomobject]@{ ExitCode = -1; Output = "(no Codex output object returned)"; TimedOut = $false }
    }
    else {
        $last = $received[-1]
        $result = [pscustomobject]@{ ExitCode = [int]$last.ExitCode; Output = [string]$last.Output; TimedOut = $false }
    }

    Set-Text -Path $LogPath -Text $result.Output
    Write-Host $result.Output
    return $result
}

function Register-CodexFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Output
    )

    $block = @"

## $(Get-UtcStamp) Codex failure

reason: $Reason
log: $LogPath

````text
$(if ($Output.Length -gt 12000) { $Output.Substring($Output.Length - 12000) } else { $Output })
````
"@
    Append-Text -Path $failureLogPath -Text $block
}

function Set-LaneBlocked {
    param([Parameter(Mandatory = $true)][string]$Reason)

    $status = @"
# Lane Status: $LaneName

Status: BLOCKED
Updated: $(Get-UtcStamp)

Reason:
$Reason

Next:
GPT review needed. Do not hammer Codex until the prompt is repaired or the blocker is cleared.
"@
    Set-Text -Path $laneStatusPath -Text $status
    Set-Text -Path $gptReviewNeededPath -Text "Codex retry failed at $(Get-UtcStamp). Reason: $Reason"
}

$runCount = 0
Write-Host "Codex supervisor loop started for '$LaneName'. Stop file: $Global:StopFile"

while (-not (Test-Path -LiteralPath $Global:StopFile -PathType Leaf)) {
    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "waiting" -Detail "Polling prompt."

    $laneStatus = Read-TextIfExists -Path $laneStatusPath
    if ($laneStatus -match "Status:\s*BLOCKED" -and (Test-Path -LiteralPath $gptReviewNeededPath -PathType Leaf)) {
        Write-Host "Lane '$LaneName' is BLOCKED and waiting for GPT review. Sleeping."
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codex) {
        Write-Warning "codex executable not found. Waiting $Global:PromptPollSeconds seconds."
        Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "missing_executable" -Detail "codex not found."
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $lanePrompt = Read-TextIfExists -Path $promptPath
    if ([string]::IsNullOrWhiteSpace($lanePrompt)) {
        $reason = "NEXT_CODEX_PROMPT.md is empty."
        Register-CodexFailure -Reason $reason -LogPath "(no run)" -Output $reason
        Set-Text -Path $gptReviewNeededPath -Text "$reason $(Get-UtcStamp)"
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $runCount = Get-AndIncrementRunCounter -Lane $lane -CounterName "codex"
    $stamp = Get-FileSafeStamp
    $summaryBefore = Get-FileWriteUtc -Path $summaryPath
    $promptBeforeHash = Get-ContentHash -Text $lanePrompt
    $logPath = Join-Path $logRoot "codex_${stamp}_run${runCount}.log"

    Write-Host ""
    Write-Host "[$(Get-UtcStamp)] Starting Codex run $runCount for '$LaneName'. Log: $logPath"
    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "running" -Detail "Run $runCount"

    $wrappedPrompt = New-CodexWorkerPrompt -LanePrompt $lanePrompt
    $result = Invoke-CodexOnce -Prompt $wrappedPrompt -LogPath $logPath

    $summaryAfter = Get-FileWriteUtc -Path $summaryPath
    $promptAfter = Read-TextIfExists -Path $promptPath
    $failReasons = @()
    if ($result.ExitCode -ne 0) { $failReasons += "codex exit code $($result.ExitCode)" }
    if (Test-CodexFailureText -Text $result.Output) { $failReasons += "failure text detected" }
    if ($summaryAfter -le $summaryBefore) { $failReasons += "LATEST_CODEX_SUMMARY.md was not updated" }
    if ([string]::IsNullOrWhiteSpace($promptAfter)) { $failReasons += "NEXT_CODEX_PROMPT.md became empty" }

    if ($failReasons.Count -gt 0) {
        $reason = $failReasons -join "; "
        Register-CodexFailure -Reason $reason -LogPath $logPath -Output $result.Output
        Write-Warning "Codex failure detected for '$LaneName': $reason"

        & (Join-Path $PSScriptRoot "build-recovery-packet.ps1") -LaneName $LaneName | Out-Null
        $recoveryPacket = Read-TextIfExists -Path $recoveryPacketPath

        $retrySucceeded = $false
        for ($retry = 1; $retry -le [int]$Global:MaxFailureRetries; $retry += 1) {
            $retryStamp = Get-FileSafeStamp
            $retryLogPath = Join-Path $logRoot "codex_${retryStamp}_run${runCount}_retry${retry}.log"
            Write-Host "Retrying Codex once with recovery packet for '$LaneName'. Retry $retry."
            Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "retrying" -Detail "Retry $retry for run $runCount"
            $retryPrompt = New-CodexWorkerPrompt -LanePrompt $lanePrompt -RecoveryPacket $recoveryPacket
            $retrySummaryBefore = Get-FileWriteUtc -Path $summaryPath
            $retryResult = Invoke-CodexOnce -Prompt $retryPrompt -LogPath $retryLogPath
            $retryPromptAfter = Read-TextIfExists -Path $promptPath
            $retryFailed = $retryResult.ExitCode -ne 0 -or
                (Test-CodexFailureText -Text $retryResult.Output) -or
                ((Get-FileWriteUtc -Path $summaryPath) -le $retrySummaryBefore) -or
                [string]::IsNullOrWhiteSpace($retryPromptAfter)

            if (-not $retryFailed) {
                $retrySucceeded = $true
                break
            }

            Register-CodexFailure -Reason "retry $retry failed" -LogPath $retryLogPath -Output $retryResult.Output
        }

        if (-not $retrySucceeded) {
            Set-LaneBlocked -Reason $reason
            Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "blocked" -Detail $reason
            Start-Sleep -Seconds $Global:PromptPollSeconds
            continue
        }
    }

    $git = Get-GitSnapshot -Lane $lane
    $meaningfulCodeChanged = Test-HasMeaningfulCodeChange -DiffNames $git.DiffNames
    $riskSensitiveChanged = Test-HasRiskSensitiveChange -DiffNames $git.DiffNames

    if (($runCount % [int]$Global:ReviewEvery) -eq 0 -and ($meaningfulCodeChanged -or $riskSensitiveChanged)) {
        $reviewMessage = @"
Codex run $runCount requested Claude review at $(Get-UtcStamp).
meaningful_code_changed=$meaningfulCodeChanged
risk_sensitive_changed=$riskSensitiveChanged
diff_hash=$($git.Hash)
"@
        Set-Text -Path $reviewMarkerPath -Text $reviewMessage
        Write-Host "Marked '$LaneName' ready for Claude review: $reviewMarkerPath"
    }

    if ($promptBeforeHash -eq (Get-ContentHash -Text (Read-TextIfExists -Path $promptPath))) {
        Write-Host "Prompt hash unchanged after Codex run; GPT prompter may refine next."
    }

    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "sleeping" -Detail "Completed run $runCount"
    Start-Sleep -Seconds $Global:PromptPollSeconds
}

Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "stopped" -Detail "STOP file detected."
Write-Host "Codex supervisor loop stopped for '$LaneName'."
