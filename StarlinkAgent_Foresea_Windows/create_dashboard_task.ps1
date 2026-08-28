param([string]$TaskName = "Foresea-Starlink-Dashboard")
$ErrorActionPreference = "Stop"
$Base = $PSScriptRoot
$Python = Join-Path $Base ".venv\Scripts\python.exe"
$Dashboard = Join-Path $Base "dashboard.py"
if (-not (Test-Path $Python)) { throw "Ambiente virtual nao encontrado. Execute install-windows.ps1 primeiro." }
$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Dashboard + '"') -WorkingDirectory $Base
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description "Dashboard web local do consumo Starlink Foresea v0.8." -Force | Out-Null
Write-Host "Tarefa criada: $TaskName (inicia com o Windows)." -ForegroundColor Green
