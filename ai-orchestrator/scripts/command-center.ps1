$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

Ensure-Directory -Path $Global:LogsRoot
$transcriptPath = Join-Path $Global:LogsRoot ("command_center_{0}.log" -f (Get-FileSafeStamp))

try {
    Start-Transcript -Path $transcriptPath -Append | Out-Null
}
catch {
    Write-Warning "Could not start transcript: $($_.Exception.Message)"
}

Set-Location $Global:RepoRoot

Write-Host ""
Write-Host "AI Orchestrator Command Center"
Write-Host "Repo root: $Global:RepoRoot"
Write-Host "Transcript: $transcriptPath"
Write-Host ""

foreach ($lane in Get-ExistingAiLanes) {
    $loopPath = Ensure-AiLoopDir -Lane $lane
    Write-Host "Lane: $($lane.Name)"
    Write-Host "  Path:                 $($lane.Path)"
    Write-Host "  Next action packet:   $(Join-Path $loopPath 'NEXT_ACTION_PACKET.md')"
    Write-Host "  Long/manual requests: $(Join-Path $loopPath 'COMMANDS_LONG_REVIEW.md')"
    Write-Host "  Command results:      $(Join-Path $loopPath 'COMMAND_RESULTS.md')"
    Write-Host "  Lane status:          $(Join-Path $loopPath 'LANE_STATUS.md')"
    Write-Host "  Failure log:          $(Join-Path $loopPath 'FAILURE_LOG.md')"
    Write-Host ""
}

Write-Host "Stop all loops with:"
Write-Host '  New-Item ".\ai-orchestrator\STOP.txt" -ItemType File -Force'
Write-Host ""
Write-Host "Quick status commands:"
Write-Host "  Get-Content .\ai-orchestrator\state\PROGRAM_STATUS.md"
Write-Host "  Get-Content .\kalshi-weather-edge\.ai_loop\LANE_STATUS.md"
Write-Host "  Get-Content .\relative-value-scanner\.ai_loop\NEXT_ACTION_PACKET.md"
Write-Host "  Get-Content .\market-graph-consistency\.ai_loop\COMMANDS_LONG_REVIEW.md"
Write-Host ""
Write-Host "Manual logged command example:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\run-manual-command-and-log.ps1`" -LaneName relative_value -Command `"pytest tests\test_matching.py -q`" -TimeoutSeconds 180"
Write-Host ""
Write-Host "Background manual command example:"
Write-Host "  powershell -ExecutionPolicy Bypass -File `"$PSScriptRoot\run-manual-command-and-log.ps1`" -LaneName weather -Command `"python main.py --help`" -Background"
Write-Host ""
Write-Host "You are now at the repo root."
