$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Ambiente virtual nao encontrado. Execute install-windows.ps1 primeiro." }
Write-Host "Dashboard Starlink v0.8: http://127.0.0.1:8787" -ForegroundColor Cyan
& $Python .\dashboard.py
