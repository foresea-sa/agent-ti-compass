$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Foresea Starlink Agent v0.8 - Instalacao Windows ===" -ForegroundColor Cyan

$PythonCmd = $null
foreach ($candidate in @("python", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $PythonCmd = $candidate
        break
    }
}
if (-not $PythonCmd) {
    throw "Python nao encontrado. Instale Python 3.11+ x64 e marque Add Python to PATH."
}

if ($PythonCmd -eq "py") {
    & py -3 -m venv .venv
} else {
    & python -m venv .venv
}

$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$Pip = Join-Path $PSScriptRoot ".venv\Scripts\pip.exe"
& $Py -m pip install --upgrade pip
& $Pip install -r requirements-windows.txt
& $Py -m playwright install chromium

foreach ($dir in @("data", "data\raw", "database", "logs", "logs\debug", "output", "assets")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot $dir) | Out-Null
}
if (-not (Test-Path .\config.json)) {
    Copy-Item .\config.example.json .\config.json
}

Write-Host ""
Write-Host "Instalacao concluida." -ForegroundColor Green
Write-Host "1) Edite config.json"
Write-Host "2) Execute: .\.venv\Scripts\python.exe credential_setup.py"
Write-Host "3) Teste login: .\.venv\Scripts\python.exe bootstrap_login.py"
Write-Host "4) Execute agente: .\.venv\Scripts\python.exe agent.py"
Write-Host "5) Agende: .\create_task.ps1 -DailyTime '06:00'"
Write-Host "6) Dashboard: .\start_dashboard.ps1"
