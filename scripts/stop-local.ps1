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
    $processId = [int]$entry.pid
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Output "PID $processId is no longer running."
        continue
    }

    $expectedExecutable = [System.IO.Path]::GetFullPath([string]$entry.executable)
    $actualExecutable = if ($process.Path) {
        [System.IO.Path]::GetFullPath([string]$process.Path)
    } else {
        ""
    }
    if ($actualExecutable -ne $expectedExecutable) {
        throw "PID $processId does not match the recorded executable. Nothing further was stopped."
    }

    try {
        $process.Kill()
        if (-not $process.WaitForExit(10000)) {
            throw "PID $processId did not exit within 10 seconds."
        }
    } catch [System.InvalidOperationException] {
        # The verified process exited between inspection and termination.
    }
    Write-Output "Stopped PID $processId."
}

Remove-Item -LiteralPath $processRecord
