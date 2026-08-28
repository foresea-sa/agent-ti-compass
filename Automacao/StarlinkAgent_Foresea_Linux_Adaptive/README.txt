FORESEA STARLINK CONSUMPTION AGENT v0.8.1.1 - LINUX
================================================

Objetivo
--------
Automatizar o login no Speedcast Compass, exportar o CSV Starlink Fleet Usage,
manter historico em SQLite, calcular tendencia/previsao, gerar PDF/Excel executivo,
expor dashboard web local e opcionalmente enviar os relatorios pelo Microsoft Graph.

Arquitetura Linux
-----------------
- Ubuntu Server 22.04/24.04 LTS x86_64
- Python 3.11+
- Playwright + Chromium headless
- SQLite
- systemd service + timer
- Segredos em /etc/starlink-agent/secrets.env
- Instalacao padrao em /opt/starlink-agent

Instalacao rapida
-----------------
chmod +x install-linux.sh
sudo ./install-linux.sh
sudo nano /opt/starlink-agent/config.json
sudo /opt/starlink-agent/credential_setup.py
sudo /opt/starlink-agent/test-login-linux.sh
sudo systemctl start starlink-agent.service
sudo /opt/starlink-agent/enable-services-linux.sh

Dashboard
---------
http://127.0.0.1:8787
Health: http://127.0.0.1:8787/health

Arquivos importantes
--------------------
/opt/starlink-agent/config.json                 configuracao
/opt/starlink-agent/database/starlink.db        historico
/opt/starlink-agent/data/compass_state.json     sessao Compass - NAO compartilhar
/opt/starlink-agent/logs/starlink-agent.log     log local
/etc/starlink-agent/secrets.env                 credenciais - protegido
/etc/systemd/system/starlink-agent.service      coleta
/etc/systemd/system/starlink-agent.timer        horario
/etc/systemd/system/starlink-dashboard.service  dashboard

Documentacao completa: LINUX_PASSO_A_PASSO.txt
