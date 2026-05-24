$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lanes.ps1")

function New-LoopCommand {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [string]$LaneName
    )

    $args = @("powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath)
    if ($LaneName) {
        $args += @("-LaneName", $LaneName)
    }
    return $args
}

function Start-WtPaneSet {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$LaneNames
    )

    if ($LaneNames.Count -lt 1) { return }

    $args = @("-w", "new", "new-tab", "--title", $Title)
    $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[0]

    if ($LaneNames.Count -ge 2) {
        $args += @(";", "split-pane", "-H")
        $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[1]
    }
    if ($LaneNames.Count -ge 3) {
        $args += @(";", "split-pane", "-V")
        $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[2]
    }

    Start-Process -FilePath "wt.exe" -ArgumentList $args
}

function Write-FallbackCommands {
    param([Parameter(Mandatory = $true)][string[]]$LaneNames)

    $scripts = Join-Path $Global:OrchestratorRoot "scripts"
    Write-Host ""
    Write-Host "Windows Terminal (wt.exe) was not found. Run these in separate visible PowerShell windows:"
    Write-Host "powershell -ExecutionPolicy Bypass -File `"$scripts\init-ai-loop.ps1`""
    foreach ($laneName in $LaneNames) {
        Write-Host "powershell -NoExit -ExecutionPolicy Bypass -File `"$scripts\run-codex-lane.ps1`" -LaneName $laneName"
    }
    foreach ($laneName in $LaneNames) {
        Write-Host "powershell -NoExit -ExecutionPolicy Bypass -File `"$scripts\run-gpt-prompter-lane.ps1`" -LaneName $laneName"
    }
    foreach ($laneName in $LaneNames) {
        Write-Host "powershell -NoExit -ExecutionPolicy Bypass -File `"$scripts\run-claude-reviewer-lane.ps1`" -LaneName $laneName"
    }
    Write-Host "powershell -NoExit -ExecutionPolicy Bypass -File `"$scripts\run-short-command-runner.ps1`""
    Write-Host "powershell -NoExit -ExecutionPolicy Bypass -File `"$scripts\command-center.ps1`""
}

$scriptsRoot = Join-Path $Global:OrchestratorRoot "scripts"
$laneNames = @("weather", "relative_value", "graph")

Write-Host "Initializing AI loop handoff files before opening visible panes."
& (Join-Path $scriptsRoot "init-ai-loop.ps1")

if (Test-Path -LiteralPath $Global:StopFile -PathType Leaf) {
    Remove-Item -LiteralPath $Global:StopFile -Force
}

$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if (-not $wt) {
    Write-FallbackCommands -LaneNames $laneNames
    exit 0
}

Write-Host "Opening visible AI orchestrator panes. Stop with:"
Write-Host "  New-Item `".\ai-orchestrator\STOP.txt`" -ItemType File -Force"

Start-WtPaneSet -Title "AI Codex supervisors" -ScriptPath (Join-Path $scriptsRoot "run-codex-lane.ps1") -LaneNames $laneNames
Start-WtPaneSet -Title "AI GPT prompters" -ScriptPath (Join-Path $scriptsRoot "run-gpt-prompter-lane.ps1") -LaneNames $laneNames
Start-WtPaneSet -Title "AI Claude Opus reviewers" -ScriptPath (Join-Path $scriptsRoot "run-claude-reviewer-lane.ps1") -LaneNames $laneNames

Start-Process -FilePath "wt.exe" -ArgumentList @("-w", "new", "new-tab", "--title", "AI short command runner", "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptsRoot "run-short-command-runner.ps1"))
Start-Process -FilePath "wt.exe" -ArgumentList @("-w", "new", "new-tab", "--title", "AI command center", "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptsRoot "command-center.ps1"))
