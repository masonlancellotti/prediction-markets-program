param(
    [string]$LaneName = "",
    [string]$PromptPath = ""
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$failures = New-Object System.Collections.Generic.List[string]
function Add-Failure { param([string]$Message) $failures.Add($Message) | Out-Null }

if ([string]::IsNullOrWhiteSpace($PromptPath)) {
    if ([string]::IsNullOrWhiteSpace($LaneName)) {
        throw "Provide -LaneName or -PromptPath."
    }
    $lane = Get-AiLane -LaneName $LaneName
    $PromptPath = Get-AiLoopFile -Lane $lane -FileName "NEXT_CODEX_PROMPT.md"
}
elseif ([string]::IsNullOrWhiteSpace($LaneName)) {
    foreach ($key in $Global:Lanes.Keys) {
        $candidate = $Global:Lanes[$key]
        if ($PromptPath -like "$($candidate.Path)*") {
            $LaneName = $key
            break
        }
    }
}

if (-not (Test-Path -LiteralPath $PromptPath -PathType Leaf)) {
    Add-Failure "Prompt file missing: $PromptPath"
}

$text = Read-TextIfExists -Path $PromptPath
$normalized = $text.ToLowerInvariant()

if ([string]::IsNullOrWhiteSpace($text) -or $normalized.Trim() -eq "no active prompt assigned.") {
    Add-Failure "Prompt is empty or only says no active prompt assigned."
}

if ($text -notmatch "(?im)^\s*Task ID\s*:") { Add-Failure "Missing Task ID." }
if ($text -notmatch "(?im)^\s*Lane\s*:") { Add-Failure "Missing Lane." }
if (-not [string]::IsNullOrWhiteSpace($LaneName) -and $text -notmatch [regex]::Escape($LaneName)) {
    Add-Failure "Prompt does not mention expected lane '$LaneName'."
}

$vaguePatterns = @(
    "continue previous work",
    "keep improving",
    "do more",
    "work on the project",
    "continue improving",
    "make better",
    "add features"
)
foreach ($pattern in $vaguePatterns) {
    if ($normalized -match [regex]::Escape($pattern)) {
        Add-Failure "Contains vague wording: $pattern"
    }
}
if ($normalized -match "\bnext steps\b" -and $normalized -notmatch "task id|allowed files|success criteria|stop conditions") {
    Add-Failure "Uses 'next steps' without concrete task scope."
}

if ($normalized -notmatch "do not trade|no live trading|do not add live trading|no trading|would add live trading") {
    Add-Failure "Missing explicit no-trading instruction."
}
if ($normalized -notmatch "do not edit \.env|\.env") {
    Add-Failure "Missing .env prohibition."
}
foreach ($term in @("auth", "order", "private-key|private key", "signing", "wallet")) {
    if ($normalized -notmatch $term) {
        Add-Failure "Missing forbidden safety term: $term"
    }
}

if ($normalized -notmatch "tests to run|required tests|required_tests" -or $normalized -notmatch "pytest|compileall|no tests.*bounded") {
    Add-Failure "Missing required tests."
}
if ($normalized -notmatch "stop conditions") { Add-Failure "Missing stop conditions." }
if ($normalized -notmatch "success criteria") { Add-Failure "Missing success criteria." }
if ($normalized -notmatch "allowed files|file scope|work directory|repo:") {
    Add-Failure "Missing allowed file scope or explicit work directory."
}

if ($normalized -match "git\s+(commit|push)|commit automatically|push automatically|auto-commit|auto push") {
    Add-Failure "Prompt asks for commit/push behavior."
}

if (-not [string]::IsNullOrWhiteSpace($LaneName)) {
    $currentLane = $Global:Lanes[$LaneName]
    foreach ($key in $Global:Lanes.Keys) {
        if ($key -eq $LaneName) { continue }
        $sibling = $Global:Lanes[$key]
        if ($normalized -match [regex]::Escape($sibling.Folder.ToLowerInvariant()) -and $normalized -notmatch "do not modify|forbidden|avoid") {
            Add-Failure "Prompt mentions sibling repo '$($sibling.Folder)' without explicit prohibition."
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Host "FAIL: NEXT_CODEX_PROMPT validation failed for $PromptPath"
    foreach ($failure in $failures) { Write-Host "  - $failure" }
    exit 1
}

Write-Host "PASS: NEXT_CODEX_PROMPT validation passed for $PromptPath"
