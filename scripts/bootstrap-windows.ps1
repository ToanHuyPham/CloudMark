[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python and winget are unavailable. Install Python 3.9+ first."
    }
    winget install --id Python.Python.3.12 --exact --accept-package-agreements --accept-source-agreements
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m cloudmark inventory
& .\.venv\Scripts\python.exe -m cloudmark doctor --packs storage,network,database,web

Write-Host "CloudMark Controller is installed. Windows benchmark tool automation is still alpha." -ForegroundColor Green
