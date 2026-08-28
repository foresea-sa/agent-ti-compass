STARLINK DASHBOARD v0.9.4

ROTAS
- /                         Capa executiva com cards por localidade.
- /unidade/<UNIDADE>        Dashboard exclusivo da unidade selecionada.
- /api/unit-pdf?unit=N09    Download do PDF executivo da unidade.
- /analise-consolidada      Analise consolidada de todas as unidades. Nao aparece na navegacao.

IMPORTANTE
A rota consolidada e apenas oculta da interface. Isso nao e um controle de seguranca.
Restrinja o acesso por firewall/VLAN ou implemente autenticacao/reverse proxy quando necessario.

CONFIGURACAO
A rota consolidada pode ser alterada em config.json:
  dashboard.consolidated_path

O dashboard usa o mesmo SQLite e analytics v0.9.2+ para todos os modos de exibicao.
