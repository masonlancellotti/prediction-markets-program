param([string]$LaneName = "")
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }
function Add-Warning { param([string]$Message) $warnings.Add($Message) | Out-Null }

function Get-MarkerText {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $pattern = "(?s)$([regex]::Escape("${Name}_START"))\s*(.*?)\s*$([regex]::Escape("${Name}_END"))"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) { return $match.Groups[1].Value.Trim() }
    return ""
}

function Test-LaneReview {
    param([Parameter(Mandatory = $true)]$Lane)
    $loopPath = Get-AiLoopPath -Lane $Lane
    $codex = Read-TextIfExists -Path (Join-Path $loopPath "LATEST_CODEX_SUMMARY.md")
    $gpt = Read-TextIfExists -Path (Join-Path $loopPath "LATEST_GPT_PROMPTER_OUTPUT.md")
    $laneStatus = Read-TextIfExists -Path (Join-Path $loopPath "LANE_STATUS.md")
    $combined = "$codex`n$gpt`n$laneStatus"
    $lower = $combined.ToLowerInvariant()
    $claudeMarker = (Get-MarkerText -Text $gpt -Name "CLAUDE_REVIEW_NEEDED").ToLowerInvariant()

    $hardTriggers = @(
        "paper_candidate_found",
        "paper candidate appeared",
        "paper candidates? count\s*:\s*[1-9]",
        "changed .*evaluator gate",
        "weakened .*evaluator gate",
        "settlement trust change",
        "trusted new settlement",
        "settlement normalization trust",
        "fee model change",
        "slippage model change",
        "gas model change",
        "graph-to-relative integration",
        "promote(d)? graph hints",
        "live execution design",
        "added .*order logic",
        "added .*auth logic",
        "added .*account logic",
        "added .*private-key",
        "added .*private key",
        "added .*signing",
        "added .*wallet"
    )
    $matched = @()
    foreach ($trigger in $hardTriggers) {
        if ($lower -match $trigger) { $matched += $trigger }
    }
    if ($matched.Count -gt 0 -and $claudeMarker -match "^no\b") {
        Add-Failure "$($Lane.Name): Claude review required by trigger(s) [$($matched -join ', ')] but CLAUDE_REVIEW_NEEDED says NO."
    }

    if ($claudeMarker -match "^yes\b" -and $matched.Count -eq 0 -and $lower -match "docs|schema|import hygiene|formatting|status") {
        Add-Warning "$($Lane.Name): Claude requested for likely docs/schema/import-hygiene work; review may be unnecessary."
    }
}

$lanes = if ([string]::IsNullOrWhiteSpace($LaneName)) { Get-ExistingAiLanes } else { @(Get-AiLane -LaneName $LaneName) }
foreach ($lane in $lanes) { Test-LaneReview -Lane $lane }

foreach ($warning in $warnings) { Write-Host "WARN: $warning" }
if ($failures.Count -gt 0) {
    Write-Host "FAIL: review gating validation failed"
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    exit 1
}

Write-Host "PASS: review gating validation passed"
