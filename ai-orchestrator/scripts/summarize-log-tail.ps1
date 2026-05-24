param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [int]$TailChars = 30000,
    [string]$OutFile
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\lanes.ps1")

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Log path does not exist: $Path"
}

$tail = Read-TextIfExists -Path $Path -TailChars $TailChars
if ($OutFile) {
    Set-Text -Path $OutFile -Text $tail
}
else {
    Write-Output $tail
}
