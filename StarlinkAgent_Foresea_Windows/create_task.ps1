param(
    [string]$TaskName = "Foresea-Starlink-Agent",
    [string]$DailyTime = "06:00"
)

$ErrorActionPreference = "Stop"
$Base = $PSScriptRoot
$Python = Join-Path $Base ".venv\Scripts\python.exe"
$Agent = Join-Path $Base "agent.py"
if (-not (Test-Path $Python)) { throw "Ambiente virtual nao encontrado. Execute install-windows.ps1 primeiro." }

$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "A tarefa deve executar com o MESMO usuario Windows usado em credential_setup.py." -ForegroundColor Yellow
Write-Host "Usuario atual: $currentIdentity"
$cred = Get-Credential -UserName $currentIdentity -Message "Informe a senha do usuario Windows para executar a tarefa mesmo sem sessao interativa."
$user = $cred.UserName
$plain = $cred.GetNetworkCredential().Password
if (-not $plain) { throw "Senha do usuario Windows nao informada." }

$Action = New-ScheduledTaskAction -Execute $Python -Argument ('"' + $Agent + '"') -WorkingDirectory $Base
$Trigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -User $user -Password $plain -RunLevel Highest -Description "Coleta diaria do consumo Starlink e gera relatorio executivo Foresea v0.8." -Force | Out-Null
$plain = $null
Write-Host "Tarefa criada: $TaskName - diariamente as $DailyTime - usuario $user" -ForegroundColor Green
