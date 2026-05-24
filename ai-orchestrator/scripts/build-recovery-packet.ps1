param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane
$git = Get-GitSnapshot -Lane $lane

$packetPath = Join-Path $loopPath "RECOVERY_CONTEXT_PACKET.md"
$packet = @"
# Recovery Context Packet

Generated: $(Get-UtcStamp)
Lane: $LaneName
Lane path: $($lane.Path)

## PROJECT_CHARTER.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "PROJECT_CHARTER.md"))

## GLOBAL_GUARDRAILS.md
$(Read-TextIfExists -Path (Join-Path $Global:ContextRoot "GLOBAL_GUARDRAILS.md"))

## PROGRAM_STATUS.md
$(Read-TextIfExists -Path (Join-Path $Global:StateRoot "PROGRAM_STATUS.md"))

## ACTIVE_GOALS.md
$(Read-TextIfExists -Path (Join-Path $Global:StateRoot "ACTIVE_GOALS.md"))

## NEXT_STEPS.md
$(Read-TextIfExists -Path (Join-Path $Global:StateRoot "NEXT_STEPS.md"))

## LANE_CONTEXT.md
$(Read-TextIfExists -Path (Join-Path $loopPath "LANE_CONTEXT.md"))

## LANE_STATUS.md
$(Read-TextIfExists -Path (Join-Path $loopPath "LANE_STATUS.md"))

## Current NEXT_CODEX_PROMPT.md
$(Read-TextIfExists -Path (Join-Path $loopPath "NEXT_CODEX_PROMPT.md"))

## Latest Codex Summary
$(Read-TextIfExists -Path (Join-Path $loopPath "LATEST_CODEX_SUMMARY.md"))

## Latest GPT Prompter Output
$(Read-TextIfExists -Path (Join-Path $loopPath "LATEST_GPT_PROMPTER_OUTPUT.md"))

## Latest Claude Review
$(Read-TextIfExists -Path (Join-Path $loopPath "LATEST_CLAUDE_REVIEW.md"))

## COMMAND_RESULTS.md tail
$(Read-TextIfExists -Path (Join-Path $loopPath "COMMAND_RESULTS.md") -TailChars $Global:CommandResultsTailChars)

## git status --short
$($git.Status)

## git diff --stat
$($git.DiffStat)

## git diff --name-only
$($git.DiffNames)

## FAILURE_LOG.md tail
$(Read-TextIfExists -Path (Join-Path $loopPath "FAILURE_LOG.md") -TailChars $Global:FailureLogTailChars)
"@

Set-Text -Path $packetPath -Text $packet
Write-Host $packetPath
