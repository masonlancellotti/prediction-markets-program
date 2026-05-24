param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane
$logRoot = Join-Path (Get-LaneLogRoot -LaneName $LaneName) "claude"
Ensure-Directory -Path $logRoot

$reviewMarkerPath = Join-Path $loopPath "READY_FOR_CLAUDE_REVIEW.txt"
$latestClaudePath = Join-Path $loopPath "LATEST_CLAUDE_REVIEW.md"
$nextPromptPath = Join-Path $loopPath "NEXT_CODEX_PROMPT.md"
$commandResultsPath = Join-Path $loopPath "COMMAND_RESULTS.md"
$latestCodexPath = Join-Path $loopPath "LATEST_CODEX_SUMMARY.md"
$laneStatusPath = Join-Path $loopPath "LANE_STATUS.md"
$reviewHashPath = Join-Path $loopPath "LAST_CLAUDE_REVIEW_DIFF_HASH.txt"
$reviewPolicyPath = Join-Path $Global:StateRoot "REVIEW_POLICY.json"

function Get-RecentCodexLogs {
    $codexLogRoot = Join-Path (Get-LaneLogRoot -LaneName $LaneName) "codex"
    if (-not (Test-Path -LiteralPath $codexLogRoot -PathType Container)) {
        return "(no Codex logs yet)"
    }

    $chunks = New-Object System.Collections.Generic.List[string]
    $files = Get-ChildItem -LiteralPath $codexLogRoot -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 3

    foreach ($file in $files) {
        $chunks.Add("### $($file.Name)")
        $chunks.Add((Read-TextIfExists -Path $file.FullName -TailChars 12000))
    }

    if ($chunks.Count -eq 0) {
        return "(no Codex logs yet)"
    }
    return ($chunks -join "`r`n")
}

function Test-DocsOnlyDiff {
    param([Parameter(Mandatory = $true)][string]$DiffNames)

    if ([string]::IsNullOrWhiteSpace($DiffNames)) { return $false }
    if ($DiffNames -match "^\(git " -or $DiffNames.Trim() -eq "(no output)") { return $false }

    foreach ($line in ($DiffNames -split "`r?`n")) {
        $name = $line.Trim()
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        if ($name -notmatch "\.(md|txt)$" -and $name -notmatch "(^|/)docs?/") {
            return $false
        }
    }
    return $true
}

function New-ClaudePacket {
    param([Parameter(Mandatory = $true)]$Git)

    $diffText = "(diff omitted; no risk-sensitive diff detected or git unavailable)"
    $riskSensitive = Test-HasRiskSensitiveChange -DiffNames $Git.DiffNames
    $meaningfulCode = Test-HasMeaningfulCodeChange -DiffNames $Git.DiffNames
    if ($riskSensitive -or $meaningfulCode) {
        $diffText = Invoke-GitText -Cwd $lane.Path -Args @("diff", "--no-ext-diff")
        if ($diffText.Length -gt $Global:ReviewDiffTailChars) {
            $diffText = $diffText.Substring(0, $Global:ReviewDiffTailChars) + "`r`n(diff truncated at $Global:ReviewDiffTailChars chars)"
        }
    }

    return @"
Lane: $LaneName
Lane path: $($lane.Path)
Review marker:
$(Read-TextIfExists -Path $reviewMarkerPath)

## Stable guardrails
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "GLOBAL_GUARDRAILS.md"))

## REVIEW_POLICY.json
$(Read-TextIfExists -Path $reviewPolicyPath)

## LANE_STATUS.md
$(Read-TextIfExists -Path $laneStatusPath)

## LATEST_CODEX_SUMMARY.md
$(Read-TextIfExists -Path $latestCodexPath)

## Recent Codex logs
$(Get-RecentCodexLogs)

## git status --short
$($Git.Status)

## git diff --stat
$($Git.DiffStat)

## git diff --name-only
$($Git.DiffNames)

## Relevant git diff
$diffText

## COMMAND_RESULTS.md tail
$(Read-TextIfExists -Path $commandResultsPath -TailChars $Global:CommandResultsTailChars)

## Current NEXT_CODEX_PROMPT.md
$(Read-TextIfExists -Path $nextPromptPath)
"@
}

function New-ClaudePrompt {
    param([Parameter(Mandatory = $true)][string]$Packet)

    return @"
You are Claude Code Opus acting as a stateless read-only reviewer. Do not edit files.

Review for correctness, tests, fake-edge risk, settlement mismatch risk, hidden behavior changes, and next-prompt quality.

Reserve special scrutiny for evaluator, matcher, settlement, fee, slippage, paper-candidate, graph hint promotion, and core edge logic.

Guardrails:
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "GLOBAL_GUARDRAILS.md"))

Return a concise review. Include a next prompt between these exact markers:
NEXT_CODEX_PROMPT_START
...
NEXT_CODEX_PROMPT_END

Packet:
$Packet
"@
}

Write-Host "Claude Opus reviewer loop started for '$LaneName'. Stop file: $Global:StopFile"

while (-not (Test-Path -LiteralPath $Global:StopFile -PathType Leaf)) {
    Set-LaneHeartbeat -Lane $lane -Role "claude" -Status "waiting" -Detail "Polling review marker."

    if (-not (Test-Path -LiteralPath $reviewMarkerPath -PathType Leaf)) {
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $git = Get-GitSnapshot -Lane $lane
    $marker = Read-TextIfExists -Path $reviewMarkerPath
    $meaningfulCode = Test-HasMeaningfulCodeChange -DiffNames $git.DiffNames
    $riskSensitive = Test-HasRiskSensitiveChange -DiffNames $git.DiffNames
    $docsOnly = Test-DocsOnlyDiff -DiffNames $git.DiffNames
    $gptRequested = $marker -match "(?i)GPT requested|Claude review needed|YES"
    $paperCandidateSignal = ((Read-TextIfExists -Path $latestCodexPath) + "`n" + (Read-TextIfExists -Path $commandResultsPath) + "`n" + $marker) -match "(?i)PAPER_CANDIDATE|paper candidate"

    $lastHash = Read-TextIfExists -Path $reviewHashPath
    if (-not [string]::IsNullOrWhiteSpace($lastHash) -and $lastHash.Trim() -eq $git.Hash) {
        $skip = "Skipped Claude review at $(Get-UtcStamp): diff-stat hash unchanged ($($git.Hash))."
        Write-Host $skip
        Set-Text -Path $latestClaudePath -Text $skip
        Remove-Item -LiteralPath $reviewMarkerPath -Force
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    if ($docsOnly -and -not $gptRequested -and -not $riskSensitive -and -not $paperCandidateSignal) {
        $skip = "Deferred Claude review at $(Get-UtcStamp): docs-only/status-only diff and no GPT risk request."
        Write-Host $skip
        Set-Text -Path $latestClaudePath -Text $skip
        Set-Text -Path $reviewHashPath -Text $git.Hash
        Remove-Item -LiteralPath $reviewMarkerPath -Force
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    if (-not ($meaningfulCode -or $riskSensitive -or $gptRequested -or $paperCandidateSignal)) {
        $skip = "Deferred Claude review at $(Get-UtcStamp): no meaningful code, risk-sensitive, or GPT review signal."
        Write-Host $skip
        Set-Text -Path $latestClaudePath -Text $skip
        Set-Text -Path $reviewHashPath -Text $git.Hash
        Remove-Item -LiteralPath $reviewMarkerPath -Force
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $claude) {
        Write-Warning "claude executable not found. Waiting $Global:PromptPollSeconds seconds."
        Set-LaneHeartbeat -Lane $lane -Role "claude" -Status "missing_executable" -Detail "claude not found."
        Start-Sleep -Seconds $Global:PromptPollSeconds
        continue
    }

    $runCount = Get-AndIncrementRunCounter -Lane $lane -CounterName "claude"
    $stamp = Get-FileSafeStamp
    $logPath = Join-Path $logRoot "claude_review_${stamp}_run${runCount}.md"
    $prompt = New-ClaudePrompt -Packet (New-ClaudePacket -Git $git)

    Write-Host "[$(Get-UtcStamp)] Starting Claude Opus review for '$LaneName'. Log: $logPath"
    Set-LaneHeartbeat -Lane $lane -Role "claude" -Status "running" -Detail "Run $runCount"

    Push-Location $lane.Path
    try {
        $output = & claude -p --model $Global:ClaudeModel $prompt 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $text = ($output -join "`r`n")
    if ($exitCode -ne 0) {
        $text = "Claude exited with code $exitCode.`r`n`r`n$text"
        Write-Warning "Claude exited with code $exitCode for '$LaneName'."
    }

    Set-Text -Path $latestClaudePath -Text $text
    Set-Text -Path $logPath -Text $text
    Set-Text -Path $reviewHashPath -Text $git.Hash

    $nextPrompt = Get-DelimitedSection -Text $text -StartMarker "NEXT_CODEX_PROMPT_START" -EndMarker "NEXT_CODEX_PROMPT_END"
    if (-not [string]::IsNullOrWhiteSpace($nextPrompt)) {
        Set-Text -Path $nextPromptPath -Text $nextPrompt
        Write-Host "Updated NEXT_CODEX_PROMPT.md from Claude markers."
    }

    Remove-Item -LiteralPath $reviewMarkerPath -Force
    Set-LaneHeartbeat -Lane $lane -Role "claude" -Status "sleeping" -Detail "Completed run $runCount"
    Start-Sleep -Seconds $Global:PromptPollSeconds
}

Set-LaneHeartbeat -Lane $lane -Role "claude" -Status "stopped" -Detail "STOP file detected."
Write-Host "Claude Opus reviewer loop stopped for '$LaneName'."
