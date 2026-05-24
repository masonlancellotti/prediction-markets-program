param(
    [switch]$GroupedWindows,
    [switch]$CodexOnly,
    [switch]$ClaudeOnly,
    [switch]$CommandsOnly,
    [switch]$MonitorOnly
)

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

function New-MonitorCommand {
    param([Parameter(Mandatory = $true)][string]$Command)
    return @("powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $Command)
}

function Start-WtPaneSet {
    param(
        [Parameter(Mandatory = $true)][string]$WindowName,
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string[]]$LaneNames
    )

    if ($LaneNames.Count -lt 1) { return }

    $args = @("-w", $WindowName, "new-tab", "--title", $Title)
    $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[0]

    if ($LaneNames.Count -ge 2) {
        $args += @(";", "split-pane", "-H", "--title", "$Title - $($LaneNames[1])")
        $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[1]
    }
    if ($LaneNames.Count -ge 3) {
        $args += @(";", "split-pane", "-V", "--title", "$Title - $($LaneNames[2])")
        $args += New-LoopCommand -ScriptPath $ScriptPath -LaneName $LaneNames[2]
    }

    Start-Process -FilePath "wt.exe" -ArgumentList $args
}

function Start-CommandsWindow {
    param(
        [Parameter(Mandatory = $true)][string]$WindowName,
        [Parameter(Mandatory = $true)][string]$ScriptsRoot
    )

    $commandCenter = Join-Path $ScriptsRoot "command-center.ps1"
    $shortRunner = Join-Path $ScriptsRoot "run-short-command-runner.ps1"
    $gptPrompter = Join-Path $ScriptsRoot "run-gpt-prompter-lane.ps1"
    $monitorCommand = "Set-Location `"$Global:RepoRoot`"; Write-Host 'AI monitor: relative_value action/failure/command files'; while (-not (Test-Path '.\ai-orchestrator\STOP.txt')) { Clear-Host; Write-Host 'NEXT_ACTION_PACKET'; Get-Content '.\relative-value-scanner\.ai_loop\NEXT_ACTION_PACKET.md' -Tail 80 -ErrorAction SilentlyContinue; Write-Host ''; Write-Host 'COMMANDS_LONG_REVIEW'; Get-Content '.\relative-value-scanner\.ai_loop\COMMANDS_LONG_REVIEW.md' -Tail 80 -ErrorAction SilentlyContinue; Write-Host ''; Write-Host 'FAILURE_LOG'; Get-Content '.\relative-value-scanner\.ai_loop\FAILURE_LOG.md' -Tail 80 -ErrorAction SilentlyContinue; Start-Sleep -Seconds 10 }; Write-Host 'STOP.txt detected. Monitor exiting.'"

    $args = @("-w", $WindowName, "new-tab", "--title", "Short command runner")
    $args += New-LoopCommand -ScriptPath $shortRunner
    $args += @(";", "split-pane", "-H", "--title", "Command center")
    $args += New-LoopCommand -ScriptPath $commandCenter
    $args += @(";", "split-pane", "-V", "--title", "GPT relative_value")
    $args += New-LoopCommand -ScriptPath $gptPrompter -LaneName "relative_value"
    $args += @(";", "split-pane", "-V", "--title", "Monitor")
    $args += New-MonitorCommand -Command $monitorCommand

    Start-Process -FilePath "wt.exe" -ArgumentList $args
}

function Write-FallbackGroup {
    param(
        [Parameter(Mandatory = $true)][string]$GroupName,
        [Parameter(Mandatory = $true)][string[]]$Commands
    )

    Write-Host ""
    Write-Host "[$GroupName]"
    foreach ($command in $Commands) { Write-Host $command }
}

function Write-FallbackCommands {
    param(
        [Parameter(Mandatory = $true)][string[]]$LaneNames,
        [Parameter(Mandatory = $true)][string]$ScriptsRoot,
        [bool]$LaunchCodex,
        [bool]$LaunchClaude,
        [bool]$LaunchCommands,
        [bool]$LaunchMonitors
    )

    Write-Host ""
    Write-Host "Windows Terminal (wt.exe) was not found. Run these grouped commands in separate visible PowerShell windows:"
    Write-Host "powershell -ExecutionPolicy Bypass -File `"$ScriptsRoot\init-ai-loop.ps1`""

    if ($LaunchCodex) {
        Write-FallbackGroup -GroupName "AI Loop - Codex" -Commands @($LaneNames | ForEach-Object { "powershell -NoExit -ExecutionPolicy Bypass -File `"$ScriptsRoot\run-codex-lane.ps1`" -LaneName $_" })
    }
    if ($LaunchClaude) {
        Write-FallbackGroup -GroupName "AI Loop - Claude" -Commands @($LaneNames | ForEach-Object { "powershell -NoExit -ExecutionPolicy Bypass -File `"$ScriptsRoot\run-claude-reviewer-lane.ps1`" -LaneName $_" })
    }
    if ($LaunchCommands) {
        Write-FallbackGroup -GroupName "AI Loop - Commands" -Commands @(
            "powershell -NoExit -ExecutionPolicy Bypass -File `"$ScriptsRoot\run-short-command-runner.ps1`"",
            "powershell -NoExit -ExecutionPolicy Bypass -File `"$ScriptsRoot\command-center.ps1`"",
            "powershell -NoExit -ExecutionPolicy Bypass -File `"$ScriptsRoot\run-gpt-prompter-lane.ps1`" -LaneName relative_value"
        )
    }
    if ($LaunchMonitors) {
        Write-FallbackGroup -GroupName "AI Loop - Monitors" -Commands @(
            "powershell -NoExit -ExecutionPolicy Bypass -Command `"Set-Location '$Global:RepoRoot'; Get-Content '.\relative-value-scanner\.ai_loop\NEXT_ACTION_PACKET.md' -Wait`"",
            "powershell -NoExit -ExecutionPolicy Bypass -Command `"Set-Location '$Global:RepoRoot'; Get-Content '.\relative-value-scanner\.ai_loop\FAILURE_LOG.md' -Wait`""
        )
    }
}

$scriptsRoot = Join-Path $Global:OrchestratorRoot "scripts"
$laneNames = @("relative_value", "graph", "weather")

$anySpecific = $CodexOnly -or $ClaudeOnly -or $CommandsOnly -or $MonitorOnly
$launchCodex = if ($anySpecific) { [bool]$CodexOnly } else { $true }
$launchClaude = if ($anySpecific) { [bool]$ClaudeOnly } else { $true }
$launchCommands = if ($anySpecific) { [bool]$CommandsOnly -or [bool]$MonitorOnly } else { $true }
$launchMonitors = [bool]$MonitorOnly -or -not $anySpecific

Write-Host "Initializing AI loop handoff files before opening visible panes."
& (Join-Path $scriptsRoot "init-ai-loop.ps1")

if (Test-Path -LiteralPath $Global:StopFile -PathType Leaf) {
    Remove-Item -LiteralPath $Global:StopFile -Force
}

$wt = Get-Command wt.exe -ErrorAction SilentlyContinue
if (-not $wt) {
    Write-FallbackCommands -LaneNames $laneNames -ScriptsRoot $scriptsRoot -LaunchCodex:$launchCodex -LaunchClaude:$launchClaude -LaunchCommands:$launchCommands -LaunchMonitors:$launchMonitors
    exit 0
}

Write-Host "Opening visible AI orchestrator panes. Stop with:"
Write-Host "  New-Item `".\ai-orchestrator\STOP.txt`" -ItemType File -Force"

if ($GroupedWindows) {
    if ($launchCodex) {
        Start-WtPaneSet -WindowName "AI Loop - Codex" -Title "AI Loop - Codex relative_value" -ScriptPath (Join-Path $scriptsRoot "run-codex-lane.ps1") -LaneNames $laneNames
    }
    if ($launchClaude) {
        Start-WtPaneSet -WindowName "AI Loop - Claude" -Title "AI Loop - Claude relative_value" -ScriptPath (Join-Path $scriptsRoot "run-claude-reviewer-lane.ps1") -LaneNames $laneNames
    }
    if ($launchCommands) {
        Start-CommandsWindow -WindowName "AI Loop - Commands" -ScriptsRoot $scriptsRoot
    }
    exit 0
}

if ($launchCodex) {
    Start-WtPaneSet -WindowName "new" -Title "AI Codex supervisors" -ScriptPath (Join-Path $scriptsRoot "run-codex-lane.ps1") -LaneNames $laneNames
}
if ($launchClaude) {
    Start-WtPaneSet -WindowName "new" -Title "AI Claude Opus reviewers" -ScriptPath (Join-Path $scriptsRoot "run-claude-reviewer-lane.ps1") -LaneNames $laneNames
}
if ($launchCommands) {
    Start-WtPaneSet -WindowName "new" -Title "AI GPT prompters" -ScriptPath (Join-Path $scriptsRoot "run-gpt-prompter-lane.ps1") -LaneNames $laneNames
    Start-Process -FilePath "wt.exe" -ArgumentList @("-w", "new", "new-tab", "--title", "AI short command runner", "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptsRoot "run-short-command-runner.ps1"))
    Start-Process -FilePath "wt.exe" -ArgumentList @("-w", "new", "new-tab", "--title", "AI command center", "powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $scriptsRoot "command-center.ps1"))
}
