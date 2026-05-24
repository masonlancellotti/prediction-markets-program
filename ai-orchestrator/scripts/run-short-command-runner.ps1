$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

New-Item -ItemType Directory -Path $Global:LogsRoot -Force | Out-Null

function Test-UnsafeShellSyntax {
    param([Parameter(Mandatory = $true)][string]$Command)

    if ($Command -match "[;&|<>``]") { return $true }
    if ($Command -match "(?i)\b(Remove-Item|rm|del|erase|rmdir|move|copy|xcopy|robocopy|curl|wget|Invoke-WebRequest|Invoke-RestMethod|Start-Process|Stop-Process|git\s+add|git\s+push|git\s+commit|git\s+reset|git\s+checkout|git\s+clean|scp|ssh|pip\s+install|npm\s+install|poetry\s+install|uv\s+pip|deploy|private[_-]?key|wallet|account|auth|order\s+submission|live\s+trading)\b") { return $true }
    return $false
}

function Test-AllowedShortCommand {
    param([Parameter(Mandatory = $true)][string]$Command)

    $trimmed = $Command.Trim()
    if (Test-UnsafeShellSyntax -Command $trimmed) { return $false }

    $patterns = @(
        '^git status --short$',
        '^git diff --stat$',
        '^git diff --name-only$',
        '^git branch --show-current$',
        '^rg\s+.+$',
        '^python -m compileall(\s+.+)?$',
        '^(python -m pytest|pytest)\s+(?!-q(\s|$)).*\s-q(\s.*)?$',
        '^(python -m pytest|pytest)\s+-q\s+.+$',
        '^python -m pytest --help$',
        '^pytest --help$',
        '^python (main|scan)\.py --help$'
    )

    foreach ($pattern in $patterns) {
        if ($trimmed -match $pattern) {
            return $true
        }
    }
    return $false
}

function Resolve-CommandCwd {
    param(
        [Parameter(Mandatory = $true)]
        $Lane,
        [Parameter(Mandatory = $true)]
        [string]$Cwd
    )

    $candidate = $Cwd
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        throw "cwd must be an absolute path inside the lane root"
    }

    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "cwd does not exist: $Cwd"
    }

    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    $laneRoot = (Resolve-Path -LiteralPath $Lane.Path).Path.TrimEnd('\')
    if (-not ($resolved.Equals($laneRoot, [System.StringComparison]::OrdinalIgnoreCase) -or $resolved.StartsWith("$laneRoot\", [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "cwd is outside the lane root: $resolved"
    }

    return $resolved
}

function Get-DoneIds {
    param([Parameter(Mandatory = $true)][string]$DonePath)

    $ids = @{}
    if (-not (Test-Path -LiteralPath $DonePath -PathType Leaf)) {
        return $ids
    }

    foreach ($line in Get-Content -LiteralPath $DonePath) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $obj = $line | ConvertFrom-Json
            if ($obj.PSObject.Properties.Name.Contains("id") -and $obj.id) { $ids[[string]$obj.id] = $true }
        }
        catch {
            continue
        }
    }
    return $ids
}

function Complete-CommandId {
    param(
        [Parameter(Mandatory = $true)][string]$DonePath,
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Reason = ""
    )

    $payload = [ordered]@{
        id = $Id
        status = $Status
        reason = $Reason
        completed_at = Get-UtcStamp
    } | ConvertTo-Json -Compress
    Add-Content -LiteralPath $DonePath -Value $payload -Encoding UTF8
}

function Append-CommandResult {
    param(
        [Parameter(Mandatory = $true)][string]$ResultsPath,
        [Parameter(Mandatory = $true)][string]$LaneName,
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Cwd,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$Reason,
        [string]$ExpectedOutput = "",
        [string]$Output = "",
        [int]$ExitCode = -1
    )

    $block = @"

## $(Get-UtcStamp) [$LaneName] $Id - $Status

cwd: $Cwd
command: $Command
reason: $Reason
expected_output: $ExpectedOutput
exit_code: $ExitCode

````text
$Output
````
"@
    Append-Text -Path $ResultsPath -Text $block
}

function Invoke-ShortCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Cwd,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $job = Start-Job -ScriptBlock {
        param($JobCwd, $JobCommand)
        Set-Location -LiteralPath $JobCwd
        $jobOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -Command $JobCommand 2>&1 | ForEach-Object { [string]$_ }
        [pscustomobject]@{
            ExitCode = $LASTEXITCODE
            Output = ($jobOutput -join "`r`n")
        }
    } -ArgumentList $Cwd, $Command

    $finished = Wait-Job -Job $job -Timeout $TimeoutSeconds
    if (-not $finished) {
        Stop-Job -Job $job -Force -ErrorAction SilentlyContinue
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        return [pscustomobject]@{
            ExitCode = -1
            Output = "Timed out after $TimeoutSeconds seconds."
        }
    }

    $received = @(Receive-Job -Job $job)
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    if ($received.Count -eq 0) {
        return [pscustomobject]@{
            ExitCode = -1
            Output = "(no command output object returned)"
        }
    }

    $result = $received[-1]
    return [pscustomobject]@{
        ExitCode = [int]$result.ExitCode
        Output = [string]$result.Output
    }
}

Write-Host "Short command runner started. Stop file: $Global:StopFile"

while (-not (Test-Path -LiteralPath $Global:StopFile -PathType Leaf)) {
    foreach ($lane in Get-ExistingAiLanes) {
        $loopPath = Ensure-AiLoopDir -Lane $lane
        $pendingPath = Join-Path $loopPath "COMMANDS_SHORT_PENDING.jsonl"
        $donePath = Join-Path $loopPath "COMMANDS_DONE.jsonl"
        $resultsPath = Join-Path $loopPath "COMMAND_RESULTS.md"

        foreach ($path in @($pendingPath, $donePath, $resultsPath)) {
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                New-Item -ItemType File -Path $path -Force | Out-Null
            }
        }

        $doneIds = Get-DoneIds -DonePath $donePath
        foreach ($line in Get-Content -LiteralPath $pendingPath) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }

            try {
                $request = $line | ConvertFrom-Json
            }
            catch {
                $id = "invalid-json-$(Get-ContentHash -Text $line)"
                $doneIds = Get-DoneIds -DonePath $donePath
                if ($doneIds.ContainsKey($id)) { continue }
                Append-CommandResult -ResultsPath $resultsPath -LaneName $lane.Name -Id $id -Status "REJECTED" -Cwd $lane.Path -Command "(invalid json)" -Reason "Pending line is not valid JSON." -Output $line
                Complete-CommandId -DonePath $donePath -Id $id -Status "REJECTED" -Reason "invalid json"
                continue
            }

            $required = @("id", "classification", "cwd", "command", "reason", "expected_output", "timeout_seconds")
            $missing = @()
            foreach ($field in $required) {
                if (-not $request.PSObject.Properties.Name.Contains($field)) {
                    $missing += $field
                    continue
                }
                if ([string]::IsNullOrWhiteSpace([string]$request.PSObject.Properties[$field].Value)) {
                    $missing += $field
                }
            }

            $id = ""
            if ($request.PSObject.Properties.Name.Contains("id")) {
                $id = [string]$request.PSObject.Properties["id"].Value
            }
            if ([string]::IsNullOrWhiteSpace($id)) {
                $id = "missing-id-$([guid]::NewGuid().ToString('N'))"
            }
            if ($doneIds.ContainsKey($id)) { continue }

            $status = "COMPLETED"
            $output = ""
            $exitCode = -1
            $reason = if ($request.PSObject.Properties.Name.Contains("reason")) { [string]$request.PSObject.Properties["reason"].Value } else { "" }
            $expectedOutput = if ($request.PSObject.Properties.Name.Contains("expected_output")) { [string]$request.PSObject.Properties["expected_output"].Value } else { "" }
            $cwdText = if ($request.PSObject.Properties.Name.Contains("cwd")) { [string]$request.PSObject.Properties["cwd"].Value } else { "" }
            $command = if ($request.PSObject.Properties.Name.Contains("command")) { [string]$request.PSObject.Properties["command"].Value } else { "" }

            try {
                if ($missing.Count -gt 0) {
                    throw "missing required fields: $($missing -join ', ')"
                }
                if ([string]$request.PSObject.Properties["classification"].Value -ne "SAFE_SHORT_AUTO") {
                    throw "classification must be exactly SAFE_SHORT_AUTO"
                }
                $timeout = [int]$request.PSObject.Properties["timeout_seconds"].Value
                if ($timeout -lt 1 -or $timeout -gt 180) {
                    throw "timeout_seconds must be between 1 and 180"
                }
                if (-not (Test-AllowedShortCommand -Command $command)) {
                    throw "command is not allowlisted"
                }

                $resolvedCwd = Resolve-CommandCwd -Lane $lane -Cwd $cwdText
                Write-Host "[$(Get-UtcStamp)] Running safe short command '$id' for lane '$($lane.Name)': $command"
                $result = Invoke-ShortCommand -Cwd $resolvedCwd -Command $command -TimeoutSeconds $timeout
                $output = $result.Output
                $exitCode = [int]$result.ExitCode
                if ($exitCode -ne 0) {
                    $status = "FAILED"
                }
                Append-CommandResult -ResultsPath $resultsPath -LaneName $lane.Name -Id $id -Status $status -Cwd $resolvedCwd -Command $command -Reason $reason -ExpectedOutput $expectedOutput -Output $output -ExitCode $exitCode
                Complete-CommandId -DonePath $donePath -Id $id -Status $status -Reason $reason
            }
            catch {
                $status = "REJECTED"
                $output = $_.Exception.Message
                Write-Warning "Rejected command '$id' for lane '$($lane.Name)': $output"
                Append-CommandResult -ResultsPath $resultsPath -LaneName $lane.Name -Id $id -Status $status -Cwd $cwdText -Command $command -Reason $reason -ExpectedOutput $expectedOutput -Output $output -ExitCode -1
                Complete-CommandId -DonePath $donePath -Id $id -Status $status -Reason $output
            }
        }
    }

    Start-Sleep -Seconds $Global:ShortCommandPollSeconds
}

Write-Host "Short command runner stopped."
