[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$processRecord = Join-Path $repoRoot ".tmp\local\processes.json"

if (-not (Test-Path $processRecord)) {
    throw "No process record exists at $processRecord. Nothing was stopped."
}

$record = Get-Content $processRecord -Raw | ConvertFrom-Json
if ($record.format -ne "cloudmark-local-processes-v1") {
    throw "Unsupported process record format. Nothing was stopped."
}
if ($record.repository_root -ne $repoRoot) {
    throw "Process record belongs to a different repository. Nothing was stopped."
}

foreach ($entry in @($record.dashboard, $record.api)) {
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Output "PID $($entry.pid) is no longer running."
        continue
    }

    $expectedExecutable = [System.IO.Path]::GetFullPath([string]$entry.executable)
    $actualExecutable = if ($process.Path) {
        [System.IO.Path]::GetFullPath([string]$process.Path)
    } else {
        ""
    }
    if ($actualExecutable -ne $expectedExecutable) {
        throw "PID $($entry.pid) does not match the recorded executable. Nothing further was stopped."
    }

    Stop-Process -Id $entry.pid
    Write-Output "Stopped PID $($entry.pid)."
}

Remove-Item -LiteralPath $processRecord
