param(
    [Parameter(Mandatory = $true)]
    [string]$LaneName,
    [Parameter(Mandatory = $true)]
    [string]$Command,
    [int]$TimeoutSeconds = 1800,
    [switch]$Background
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

$lane = Get-AiLane -LaneName $LaneName
$loopPath = Ensure-AiLoopFileSet -Lane $lane
$resultsPath = Join-Path $loopPath "COMMAND_RESULTS.md"
$backgroundJobsPath = Join-Path $loopPath "BACKGROUND_JOBS.md"
$logRoot = Join-Path (Get-LaneLogRoot -LaneName $LaneName) "commands"
Ensure-Directory -Path $logRoot

$stamp = Get-FileSafeStamp
$logPath = Join-Path $logRoot "manual_${stamp}.log"
$errPath = Join-Path $logRoot "manual_${stamp}.err.log"

Write-Host "Manual command lane '$LaneName'"
Write-Host "cwd: $($lane.Path)"
Write-Host "command: $Command"
Write-Host "log: $logPath"

if ($Background) {
    $process = Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command) `
        -WorkingDirectory $lane.Path `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError $errPath `
        -PassThru `
        -WindowStyle Hidden

    $jobBlock = @"

## $(Get-UtcStamp) [$LaneName] background manual command

pid: $($process.Id)
cwd: $($lane.Path)
command: $Command
stdout_log: $logPath
stderr_log: $errPath
started_at: $(Get-UtcStamp)
"@
    Append-Text -Path $backgroundJobsPath -Text $jobBlock
    Append-Text -Path $resultsPath -Text "$jobBlock`r`nStatus: STARTED_BACKGROUND`r`n"
    Write-Host "Started background process PID $($process.Id). Details: $backgroundJobsPath"
    exit 0
}

$job = Start-Job -ScriptBlock {
    param($JobCwd, $JobCommand)
    Set-Location -LiteralPath $JobCwd
    $jobOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $JobCommand 2>&1 | ForEach-Object { [string]$_ }
    [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output = ($jobOutput -join "`r`n")
    }
} -ArgumentList $lane.Path, $Command

$finished = Wait-Job -Job $job -Timeout $TimeoutSeconds
if (-not $finished) {
    Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    $textOutput = "Timed out after $TimeoutSeconds seconds."
    Set-Text -Path $logPath -Text $textOutput
    $exitCode = -1
}
else {
    $received = @(Receive-Job -Job $job)
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    if ($received.Count -eq 0) {
        $exitCode = -1
        $textOutput = "(no output object returned)"
    }
    else {
        $result = $received[-1]
        $exitCode = [int]$result.ExitCode
        $textOutput = [string]$result.Output
    }
    Set-Text -Path $logPath -Text $textOutput
}

Write-Host $textOutput

$block = @"

## $(Get-UtcStamp) [$LaneName] manual command

cwd: $($lane.Path)
command: $Command
timeout_seconds: $TimeoutSeconds
exit_code: $exitCode
log: $logPath

````text
$textOutput
````
"@

Append-Text -Path $resultsPath -Text $block

if ($exitCode -ne 0) {
    throw "Manual command exited with code $exitCode. Output was logged to $resultsPath and $logPath."
}
