$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

Ensure-Directory -Path $Global:LogsRoot
Ensure-Directory -Path $Global:ContextRoot
Ensure-Directory -Path $Global:StateRoot

if (Test-Path -LiteralPath $Global:StopFile -PathType Leaf) {
    Remove-Item -LiteralPath $Global:StopFile -Force
    Write-Host "Removed old STOP file: $Global:StopFile"
}

$requiredContext = @{}
foreach ($fileName in $Global:StableContextFiles) {
    $path = Join-Path $Global:ContextRoot $fileName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $requiredContext[$fileName] = $true
        Ensure-TextFileIfMissing -Path $path -Content "# $($fileName -replace '\.md$','')`r`n"
    }
}

foreach ($fileName in $Global:StateFiles) {
    $path = Join-Path $Global:StateRoot $fileName
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Ensure-TextFileIfMissing -Path $path -Content "# $($fileName -replace '\.md$','')`r`n"
    }
}

$existingLanes = Get-ExistingAiLanes
if ($existingLanes.Count -eq 0) {
    Write-Warning "No configured lane folders exist. Edit ai-orchestrator/lanes.ps1."
}

foreach ($lane in $existingLanes) {
    $loopPath = Ensure-AiLoopFileSet -Lane $lane
    Write-Host "Initialized lane '$($lane.Name)' at $loopPath"

    foreach ($subdir in @("codex", "gpt-prompter", "claude", "commands", "recovery")) {
        Ensure-Directory -Path (Join-Path (Get-LaneLogRoot -LaneName $lane.Name) $subdir)
    }
}

Write-Host ""
Write-Host "AI loop initialization complete."
Write-Host "Stop all loops later with:"
Write-Host "  New-Item `".\ai-orchestrator\STOP.txt`" -ItemType File -Force"
