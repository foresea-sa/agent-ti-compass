class StarlinkAPICollector:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def collect(self):
        raise NotImplementedError(
            "Coletor Starlink API preparado como alternativa futura. "
            "Ative quando houver credenciais/API corporativa disponivel."
        )
