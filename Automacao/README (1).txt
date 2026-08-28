DASHBOARD STARLINK v0.8 - LINUX
==============================
O dashboard le diretamente database/starlink.db.

Servico:
  sudo systemctl start starlink-dashboard.service
  sudo systemctl enable starlink-dashboard.service

Acesso padrao:
  http://127.0.0.1:8787

Health check:
  curl http://127.0.0.1:8787/health

Para acesso remoto, altere dashboard.host para 0.0.0.0 no config.json e proteja a porta
8787 com firewall/VLAN/VPN. Nao exponha diretamente a Internet.
