param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane

$nextPromptPath = Join-Path $loopPath "NEXT_CODEX_PROMPT.md"
$nextActionPath = Join-Path $loopPath "NEXT_ACTION_PACKET.md"
$latestGptPath = Join-Path $loopPath "LATEST_GPT_PROMPTER_OUTPUT.md"
$shortPendingPath = Join-Path $loopPath "COMMANDS_SHORT_PENDING.jsonl"
$longReviewPath = Join-Path $loopPath "COMMANDS_LONG_REVIEW.md"

$taskId = "dry-run-$LaneName"
$stamp = Get-UtcStamp
$laneRoot = (Resolve-Path -LiteralPath $lane.Path).Path

$prompt = @"
# Codex Task

Task ID: $taskId
Lane: $LaneName

Do not modify project strategy code. This is a no-op dry-run handoff. Read lane `.ai_loop` files and produce a structured `LATEST_CODEX_SUMMARY.md` only if Mason explicitly starts Codex later.

Success criteria:
- Confirm the orchestrator can write a bounded task.
- Do not run tests.
- Do not request Claude.

Stop conditions:
- Any task would touch trading/auth/order/account/private-key logic.
"@

$packet = @"
# Next Action Packet

Lane: $LaneName
Task ID: $taskId
Owner: Mason / dry run
Generated: $stamp

This no-API dry run confirms the GPT handoff files can be written without calling OpenAI, Claude, or Codex.
"@

$gptOutput = @"
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
$prompt
NEXT_CODEX_PROMPT_END
SHORT_COMMANDS_JSONL_START
{"id":"dry-run-$LaneName-git-status","classification":"SAFE_SHORT_AUTO","cwd":"$($laneRoot -replace '\\','\\')","command":"git status --short","reason":"Dry-run command handoff schema check.","expected_output":"Short git status or a clear git unavailable message.","timeout_seconds":30}
SHORT_COMMANDS_JSONL_END
LONG_COMMANDS_MD_START
UNCHANGED
LONG_COMMANDS_MD_END
NEXT_ACTION_PACKET_START
$packet
NEXT_ACTION_PACKET_END
CLAUDE_REVIEW_NEEDED_START
NO - dry run only.
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
dry-run-local
MODEL_USED_END
REASONING_SUMMARY_START
No-API dry run wrote one bounded fake task and one safe command request.
REASONING_SUMMARY_END
"@

Set-Text -Path $nextPromptPath -Text $prompt
Set-Text -Path $nextActionPath -Text $packet
Set-Text -Path $latestGptPath -Text $gptOutput
Add-Content -LiteralPath $shortPendingPath -Value "{""id"":""dry-run-$LaneName-git-status"",""classification"":""SAFE_SHORT_AUTO"",""cwd"":""$($laneRoot -replace '\\','\\')"",""command"":""git status --short"",""reason"":""Dry-run command handoff schema check."",""expected_output"":""Short git status or a clear git unavailable message."",""timeout_seconds"":30}" -Encoding UTF8
if (-not (Test-Path -LiteralPath $longReviewPath -PathType Leaf)) {
    Set-Text -Path $longReviewPath -Text "# Long/Manual Command Requests`r`n"
}

Write-Host "Dry-run GPT handoff written for '$LaneName'."
Write-Host "  $nextPromptPath"
Write-Host "  $nextActionPath"
Write-Host "  $latestGptPath"
