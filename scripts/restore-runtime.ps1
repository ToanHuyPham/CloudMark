[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$RuntimePath = ".cloudmark",
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (Test-Path $venvPython) {
    $python = $venvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = (Get-Command py).Source
} else {
    throw "Python was not found. Create .venv or install Python 3.9+."
}

$arguments = @()
if ([System.IO.Path]::GetFileNameWithoutExtension($python) -eq "py") {
    $arguments += "-3"
}
$arguments += @(
    (Join-Path $PSScriptRoot "runtime_restore.py"),
    "--backup", $BackupPath,
    "--target", (Join-Path $repoRoot $RuntimePath)
)
if ($Replace) { $arguments += "--replace" }

& $python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "CloudMark runtime restore failed with exit code $LASTEXITCODE."
}
