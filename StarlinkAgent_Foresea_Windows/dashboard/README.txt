DASHBOARD WEB LOCAL v0.8
=======================

INICIAR MANUALMENTE
-------------------
.\start_dashboard.ps1

ou:
.\.venv\Scripts\python.exe dashboard.py

Padrao: http://127.0.0.1:8787

INICIAR COM O WINDOWS
---------------------
Abra PowerShell como Administrador e execute:
.\create_dashboard_task.ps1

CONFIGURACAO
------------
No config.json:
  "dashboard": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8787,
    "refresh_seconds": 60,
    "default_days": 7
  }

ACESSO REMOTO
-------------
Por seguranca, o padrao e somente localhost. Para disponibilizar na rede, altere host para
"0.0.0.0" e publique a porta SOMENTE na VLAN/rede administrativa necessaria.

IMPORTANTE: a v0.8 nao possui autenticacao web propria. Nao exponha a porta diretamente
na Internet. Se precisar acesso corporativo remoto, prefira reverse proxy com autenticacao,
VPN ou uma camada de SSO.

ENDPOINTS
---------
/                 Dashboard HTML
/api/dashboard    JSON do painel (?days=7 ou ?days=30)
/health            Saude do servico
