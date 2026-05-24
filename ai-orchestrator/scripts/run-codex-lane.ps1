param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName,
    [switch]$PrintCodexCommandOnly,
    [switch]$NoCodexSmoke
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
$blockedReasonPath = Join-Path $loopPath "BLOCKED_REASON.md"
$maxCodexArgChars = 6000

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

function Get-BundledNodePath {
    $candidate = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) { return $node.Source }
    return ""
}

function Get-CodexJsPath {
    $candidate = Join-Path $env:APPDATA "npm\node_modules\@openai\codex\bin\codex.js"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return ""
}

function Get-CodexInvocation {
    $nodePath = Get-BundledNodePath
    $codexJsPath = Get-CodexJsPath
    if (-not [string]::IsNullOrWhiteSpace($nodePath) -and -not [string]::IsNullOrWhiteSpace($codexJsPath)) {
        return [pscustomobject]@{
            FilePath = $nodePath
            Arguments = @($codexJsPath, "exec", "--sandbox", "workspace-write", "--cd", $lane.Path, "-")
            Display = "`"$nodePath`" `"$codexJsPath`" exec --sandbox workspace-write --cd `"$($lane.Path)`" -"
            Mode = "stdin_written_and_closed"
        }
    }

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if ($codex) {
        return [pscustomobject]@{
            FilePath = $codex.Source
            Arguments = @("exec", "--sandbox", "workspace-write", "--cd", $lane.Path, "-")
            Display = "`"$($codex.Source)`" exec --sandbox workspace-write --cd `"$($lane.Path)`" -"
            Mode = "stdin_written_and_closed"
        }
    }

    throw "codex executable not found and npm Codex JS entrypoint is unavailable."
}

function Get-ArgLength {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    return (($Arguments | ForEach-Object { [string]$_ }) -join " ").Length
}

function ConvertTo-NativeArgument {
    param([AllowNull()][string]$Value)

    if ($null -eq $Value) { return '""' }
    $text = [string]$Value
    if ($text.Length -eq 0) { return '""' }
    if ($text -notmatch '[\s"]') { return $text }

    $escaped = $text -replace '\\', '\\' -replace '"', '\"'
    return '"' + $escaped + '"'
}

function Join-NativeArguments {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    return (($Arguments | ForEach-Object { ConvertTo-NativeArgument -Value $_ }) -join " ")
}

function Stop-ProcessTreeSafe {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)

    if ($Process.HasExited) { return }

    try {
        & taskkill.exe /PID $Process.Id /T /F | Out-Null
    }
    catch {
        try { $Process.Kill() } catch { }
    }
}

function New-CodexWorkerPrompt {
    param(
        [Parameter(Mandatory = $true)][string]$LanePrompt,
        [switch]$RecoveryRetry
    )

    $recoverySection = ""
    if ($RecoveryRetry) {
        $recoverySection = @"

RECOVERY RETRY:
- Read `.ai_loop/RECOVERY_CONTEXT_PACKET.md` from the lane repo for recovery context.
- Read `.ai_loop/NEXT_CODEX_PROMPT.md` for the active task.
- Continue the same task only if it remains safe and bounded.
- If recovery context indicates the task is stale, unsafe, or blocked, update `.ai_loop/LATEST_CODEX_SUMMARY.md`, `.ai_loop/LANE_STATUS.md`, and `.ai_loop/NEXT_CODEX_PROMPT.md` accordingly and stop.
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
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$PromptFilePath
    )

    Set-Text -Path $PromptFilePath -Text $Prompt
    $promptChars = ([string]$Prompt).Length
    $invocation = Get-CodexInvocation
    $argLength = Get-ArgLength -Arguments $invocation.Arguments
    $prelude = @"
Codex invocation metadata
timestamp: $(Get-UtcStamp)
mode: $($invocation.Mode)
prompt_file: $PromptFilePath
prompt_chars: $promptChars
command_arg_count: $($invocation.Arguments.Count)
command_arg_chars: $argLength
command: $($invocation.Display)
stdin_will_be_written: true
stdin_will_be_closed: true

"@
    Set-Text -Path $LogPath -Text $prelude
    Write-Host $prelude

    if ($argLength -gt $maxCodexArgChars) {
        $text = "Codex command arguments are too long ($argLength chars). Refusing to invoke Codex; prompt content must be passed by stdin/file only."
        Append-Text -Path $LogPath -Text $text
        return [pscustomobject]@{ ExitCode = -1; Output = $text; TimedOut = $false }
    }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = New-Object System.Diagnostics.ProcessStartInfo
    $process.StartInfo.FileName = $invocation.FilePath
    $process.StartInfo.Arguments = Join-NativeArguments -Arguments $invocation.Arguments
    $process.StartInfo.WorkingDirectory = $lane.Path
    $process.StartInfo.UseShellExecute = $false
    $process.StartInfo.RedirectStandardInput = $true
    $process.StartInfo.RedirectStandardOutput = $true
    $process.StartInfo.RedirectStandardError = $true
    $process.StartInfo.CreateNoWindow = $true

    try {
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        $process.StandardInput.Write($Prompt)
        $process.StandardInput.Close()
        Append-Text -Path $LogPath -Text "stdin_written: true`r`nstdin_closed: true`r`nprocess_id: $($process.Id)`r`n`r`n"

        $finished = $process.WaitForExit([int]$Global:CodexTimeoutSeconds * 1000)
    }
    catch {
        $text = "Codex process failed before completion: $($_.Exception.Message)"
        Append-Text -Path $LogPath -Text $text
        try {
            if ($process -and -not $process.HasExited) { Stop-ProcessTreeSafe -Process $process }
        }
        catch { }
        return [pscustomobject]@{ ExitCode = -1; Output = $text; TimedOut = $false }
    }

    if (-not $finished) {
        Stop-ProcessTreeSafe -Process $process
        $text = "Codex timed out after $Global:CodexTimeoutSeconds seconds."
        Append-Text -Path $LogPath -Text $text
        Write-Warning $text
        return [pscustomobject]@{ ExitCode = -1; Output = $text; TimedOut = $true }
    }

    $stdout = [string]$stdoutTask.Result
    $stderr = [string]$stderrTask.Result
    $combined = @()
    if (-not [string]::IsNullOrWhiteSpace($stdout)) { $combined += $stdout }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) { $combined += "STDERR:`r`n$stderr" }
    $output = ($combined -join "`r`n")
    if ([string]::IsNullOrWhiteSpace($output)) { $output = "(Codex produced no stdout/stderr output.)" }

    $result = [pscustomobject]@{ ExitCode = [int]$process.ExitCode; Output = $output; TimedOut = $false }

    Append-Text -Path $LogPath -Text $result.Output
    Write-Host $result.Output
    return $result
}

function Write-CodexCommandPreview {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$PromptFilePath
    )

    Set-Text -Path $PromptFilePath -Text $Prompt
    $invocation = Get-CodexInvocation
    $argLength = Get-ArgLength -Arguments $invocation.Arguments
    $preview = @"
Codex command preview
mode: $($invocation.Mode)
prompt_file: $PromptFilePath
prompt_chars: $(([string]$Prompt).Length)
command_arg_count: $($invocation.Arguments.Count)
command_arg_chars: $argLength
command: $($invocation.Display)
stdin_will_be_written: true
stdin_will_be_closed: true
"@
    Write-Host $preview
    if ($argLength -gt $maxCodexArgChars) {
        throw "Codex command arguments are too long ($argLength chars). Prompt must be passed by stdin/file only."
    }
}

function Register-CodexFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$LogPath,
        [Parameter(Mandatory = $true)][string]$Output
    )

    $outputText = [string]$Output
    $outputTail = if ($outputText.Length -gt 12000) { $outputText.Substring($outputText.Length - 12000) } else { $outputText }
    $block = @"

## $(Get-UtcStamp) Codex failure

reason: $Reason
log: $LogPath

````text
$outputTail
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
    Set-Text -Path $blockedReasonPath -Text $Reason
}

$runCount = 0

if ($PrintCodexCommandOnly -or $NoCodexSmoke) {
    $lanePrompt = Read-TextIfExists -Path $promptPath
    if ([string]::IsNullOrWhiteSpace($lanePrompt)) {
        throw "NEXT_CODEX_PROMPT.md is empty."
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-next-codex-prompt.ps1") -LaneName $LaneName
    if ($LASTEXITCODE -ne 0) {
        throw "NEXT_CODEX_PROMPT.md failed autonomy prompt-quality validation."
    }

    $stamp = Get-FileSafeStamp
    $debugPromptPath = Join-Path $logRoot "codex_${stamp}_debug_prompt.md"
    $wrappedPrompt = New-CodexWorkerPrompt -LanePrompt $lanePrompt
    Write-CodexCommandPreview -Prompt $wrappedPrompt -PromptFilePath $debugPromptPath

    if ($PrintCodexCommandOnly) {
        Write-Host "PrintCodexCommandOnly complete. Codex was not launched."
        exit 0
    }

    $summary = @"
# Latest Codex Summary

task id: no-codex-smoke
lane: $LaneName
files changed: none
commands run: none
tests run: none
pass/fail: pass
paper candidates count: 0
risk flags: none
Claude review needed: no
recommended next task: existing NEXT_CODEX_PROMPT.md remains active
docs/state updated: LATEST_CODEX_SUMMARY.md smoke only

NoCodexSmoke completed at $(Get-UtcStamp). Codex was not launched.
Prompt file prepared for command-shape validation:
$debugPromptPath

Prompt passing mode:
stdin_written_and_closed

NoCodexSmoke simulated writing $(([string]$wrappedPrompt).Length) chars to redirected stdin and closing stdin. Codex was not launched.
"@
    Set-Text -Path $summaryPath -Text $summary
    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "no_codex_smoke_completed" -Detail "NoCodexSmoke completed without launching Codex."
    Write-Host "NoCodexSmoke simulated stdin write+close for $(([string]$wrappedPrompt).Length) chars. Codex was not launched."
    Write-Host "NoCodexSmoke complete. Fake summary written: $summaryPath"
    exit 0
}

Write-Host "Codex supervisor loop started for '$LaneName'. Stop file: $Global:StopFile"

while (-not (Test-Path -LiteralPath $Global:StopFile -PathType Leaf)) {
    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "waiting" -Detail "Polling prompt."

    $laneStatus = Read-TextIfExists -Path $laneStatusPath
    if ($laneStatus -match "Status:\s*BLOCKED" -and (Test-Path -LiteralPath $gptReviewNeededPath -PathType Leaf)) {
        Write-Host "Lane '$LaneName' is BLOCKED and waiting for GPT review. Sleeping."
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    try {
        $null = Get-CodexInvocation
    }
    catch {
        Write-Warning "$($_.Exception.Message) Waiting $Global:PromptPollSeconds seconds."
        Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "missing_executable" -Detail $_.Exception.Message
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

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "test-next-codex-prompt.ps1") -LaneName $LaneName
    if ($LASTEXITCODE -ne 0) {
        $reason = "NEXT_CODEX_PROMPT.md failed autonomy prompt-quality validation. Codex was not launched."
        Register-CodexFailure -Reason $reason -LogPath "(preflight)" -Output $reason
        Set-Text -Path $gptReviewNeededPath -Text "$reason $(Get-UtcStamp)"
        Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "prompt_quality_failed" -Detail $reason
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $runCount = Get-AndIncrementRunCounter -Lane $lane -CounterName "codex"
    $stamp = Get-FileSafeStamp
    $summaryBefore = Get-FileWriteUtc -Path $summaryPath
    $promptBeforeHash = Get-ContentHash -Text $lanePrompt
    $logPath = Join-Path $logRoot "codex_${stamp}_run${runCount}.log"
    $promptFilePath = Join-Path $logRoot "codex_${stamp}_run${runCount}_prompt.md"

    Write-Host ""
    Write-Host "[$(Get-UtcStamp)] Starting Codex run $runCount for '$LaneName'. Log: $logPath"
    Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "running" -Detail "Run $runCount"

    $wrappedPrompt = New-CodexWorkerPrompt -LanePrompt $lanePrompt
    $result = Invoke-CodexOnce -Prompt $wrappedPrompt -LogPath $logPath -PromptFilePath $promptFilePath

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

        $retrySucceeded = $false
        for ($retry = 1; $retry -le [int]$Global:MaxFailureRetries; $retry += 1) {
            $retryStamp = Get-FileSafeStamp
            $retryLogPath = Join-Path $logRoot "codex_${retryStamp}_run${runCount}_retry${retry}.log"
            $retryPromptFilePath = Join-Path $logRoot "codex_${retryStamp}_run${runCount}_retry${retry}_prompt.md"
            Write-Host "Retrying Codex once with recovery packet for '$LaneName'. Retry $retry."
            Set-LaneHeartbeat -Lane $lane -Role "codex" -Status "retrying" -Detail "Retry $retry for run $runCount"
            $retryPrompt = New-CodexWorkerPrompt -LanePrompt $lanePrompt -RecoveryRetry
            $retrySummaryBefore = Get-FileWriteUtc -Path $summaryPath
            $retryResult = Invoke-CodexOnce -Prompt $retryPrompt -LogPath $retryLogPath -PromptFilePath $retryPromptFilePath
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
