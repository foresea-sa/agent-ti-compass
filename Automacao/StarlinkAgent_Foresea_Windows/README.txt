FORESEA STARLINK CONSUMPTION AGENT v0.8 - WINDOWS
=================================================

Objetivo
--------
Automatizar o login no Speedcast Compass, exportar o CSV Starlink Fleet Usage,
manter historico em SQLite, calcular tendencia/previsao, gerar PDF/Excel executivo,
expor dashboard web local e opcionalmente enviar os relatorios pelo Microsoft Graph.

Arquitetura Windows
-------------------
- Windows Server 2019/2022/2025 ou Windows 10/11 x64
- Python 3.11+
- Playwright + Chromium
- Windows Credential Manager via keyring
- SQLite
- Windows Task Scheduler
- Dashboard web local

Instalacao rapida
-----------------
PowerShell Administrador:
  Set-ExecutionPolicy -Scope Process Bypass
  .\install-windows.ps1
  .\.venv\Scripts\python.exe credential_setup.py
  .\.venv\Scripts\python.exe bootstrap_login.py
  .\.venv\Scripts\python.exe agent.py
  .\create_task.ps1 -DailyTime "06:00"
  .\start_dashboard.ps1

Dashboard: http://127.0.0.1:8787
Health:    http://127.0.0.1:8787/health

Preserve database\starlink.db durante upgrades e nunca compartilhe data\compass_state.json.
Documentacao completa: WINDOWS_PASSO_A_PASSO.txt
