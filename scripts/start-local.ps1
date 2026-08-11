[CmdletBinding()]
param(
    [int]$DashboardPort = 0,
    [string]$RuntimePath = ".cloudmark",
    [string]$PythonPath = "",
    [string]$NodePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ApiPort = 8787
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$vinext = Join-Path $repoRoot "node_modules\vinext\dist\cli.js"
$localState = Join-Path $repoRoot ".tmp\local"
$processRecord = Join-Path $localState "processes.json"

function Repair-ProcessPathEnvironment {
    $environment = [System.Environment]::GetEnvironmentVariables()
    $pathKeys = @($environment.Keys | Where-Object { [string]$_ -ieq "PATH" })
    if ($pathKeys.Count -le 1) { return }

    # Some managed shells provide both Path and PATH. Windows treats them as
    # the same variable, while Start-Process rejects the duplicate dictionary.
    $orderedKeys = @($pathKeys | Sort-Object { if ([string]$_ -ceq "PATH") { 0 } else { 1 } })
    $pathValue = ($orderedKeys | ForEach-Object { [string]$environment[$_] }) -join ";"
    [System.Environment]::SetEnvironmentVariable(
        "PATH", $null, [System.EnvironmentVariableTarget]::Process
    )
    [System.Environment]::SetEnvironmentVariable(
        "Path", $pathValue, [System.EnvironmentVariableTarget]::Process
    )
}

function Test-TcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync("127.0.0.1", $Port)
        if (-not $task.Wait(300)) { return $false }
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Wait-Http([string]$Uri, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for $Uri"
}

Repair-ProcessPathEnvironment

if ($PythonPath) {
    $python = (Resolve-Path $PythonPath).Path
} elseif (Test-Path $venvPython) {
    $python = $venvPython
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
} else {
    throw "Python was not found. Create .venv or pass -PythonPath."
}
if ($NodePath) {
    $node = (Resolve-Path $NodePath).Path
} elseif (Get-Command node -ErrorAction SilentlyContinue) {
    $node = (Get-Command node).Source
} else {
    throw "Node.js was not found. Install Node.js 22+ or pass -NodePath."
}
if (-not (Test-Path $vinext)) {
    throw "Dashboard dependencies are missing. Run pnpm install."
}
if (Test-TcpPort $ApiPort) {
    throw "Port $ApiPort is already in use. Verify the existing process before starting CloudMark."
}

if ($DashboardPort -eq 0) {
    foreach ($candidate in 3000..3010) {
        if (-not (Test-TcpPort $candidate)) {
            $DashboardPort = $candidate
            break
        }
    }
}
if ($DashboardPort -lt 3000 -or $DashboardPort -gt 3010) {
    throw "DashboardPort must be between 3000 and 3010."
}
if (Test-TcpPort $DashboardPort) {
    throw "Dashboard port $DashboardPort is already in use."
}

New-Item -ItemType Directory -Force -Path $localState | Out-Null
$runtime = Join-Path $repoRoot $RuntimePath
$controllerOut = Join-Path $localState "controller.out.log"
$controllerErr = Join-Path $localState "controller.err.log"
$dashboardOut = Join-Path $localState "dashboard.out.log"
$dashboardErr = Join-Path $localState "dashboard.err.log"

$controller = Start-Process -FilePath $python `
    -ArgumentList @("-m", "cloudmark", "serve", "--port", "$ApiPort", "--data-dir", "`"$runtime`"") `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $controllerOut `
    -RedirectStandardError $controllerErr `
    -WindowStyle Hidden `
    -PassThru

try {
    Wait-Http "http://127.0.0.1:$ApiPort/api/v1/health" 20

    $dashboard = Start-Process -FilePath $node `
        -ArgumentList @("`"$vinext`"", "dev", "--hostname", "127.0.0.1", "--port", "$DashboardPort") `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $dashboardOut `
        -RedirectStandardError $dashboardErr `
        -WindowStyle Hidden `
        -PassThru

    Wait-Http "http://127.0.0.1:$DashboardPort/" 30
} catch {
    if ($dashboard -and -not $dashboard.HasExited) { Stop-Process -Id $dashboard.Id }
    if (-not $controller.HasExited) { Stop-Process -Id $controller.Id }
    throw
}

$record = [ordered]@{
    format = "cloudmark-local-processes-v1"
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    repository_root = $repoRoot
    api = [ordered]@{
        pid = $controller.Id
        url = "http://127.0.0.1:$ApiPort"
        executable = $python
    }
    dashboard = [ordered]@{
        pid = $dashboard.Id
        url = "http://localhost:$DashboardPort"
        executable = $node
    }
}
$record | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $processRecord

Write-Output "CloudMark Controller: http://127.0.0.1:$ApiPort"
Write-Output "CloudMark dashboard:  http://localhost:$DashboardPort"
Write-Output "Process record:       $processRecord"
Write-Output "Controller token was not printed. Retrieve it locally from .cloudmark\controller.token."
