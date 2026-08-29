FORESEA STARLINK CONSUMPTION AGENT v0.9.7 - LINUX
=================================================

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
sudo /opt/starlink-agent/configure-credentials.sh
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

v0.9.0 Linux - video de diagnostico
O teste de login grava a navegacao headless e gera um MP4 compacto em logs/debug/videos.
Use: sudo /opt/starlink-agent/last-video-linux.sh

v0.9.3 - Correcao de intervalos e analytics
----------------------------
CSVs de um dia sao consumo diario; CSVs multi-dia sao agregados do intervalo. O dashboard reconstrui o acumulado do ciclo e calcula GB/dia pelos arquivos diarios reais.
Para sincronizar todos os CSVs existentes em data/raw e atualizar o dashboard:
  sudo /opt/starlink-agent/sync-history-linux.sh
Detalhes: CORRECAO_v0.9.3_INTERVALOS.txt

HTTPS / PRODUCAO (v0.9.4)
-------------------------
Para publicar o Dashboard em HTTPS/TCP 443 com Caddy e manter o backend restrito a localhost:
  sudo /opt/starlink-agent/configure-caddy-linux.sh

Diagnostico:
  sudo /opt/starlink-agent/diagnostico-caddy-linux.sh

A pagina consolidada e seu endpoint dedicado ficam protegidos por Basic Auth no reverse proxy.
Consulte CADDY_HTTPS_v0.9.4.txt.
