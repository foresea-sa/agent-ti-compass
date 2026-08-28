from __future__ import annotations

import json
import os
import platform
import logging
from pathlib import Path

from collectors.compass import CompassCollector
from utils.secrets import secret_source

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()
    # Windows opens a visible browser by default. Headless Linux servers automatically
    # use headless mode. STARLINK_HEADLESS_TEST=1 can force headless on any platform.
    force_headless = os.environ.get("STARLINK_HEADLESS_TEST", "").strip().lower() in {"1", "true", "yes", "sim"}
    no_display_linux = platform.system().lower() == "linux" and not os.environ.get("DISPLAY")
    config.setdefault("compass", {})["headless"] = bool(force_headless or no_display_linux)
    logger = logging.getLogger("compass-login-test")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    print("Teste de login automatico do Compass.")
    mode = "headless" if config["compass"].get("headless") else "visivel"
    print(f"O navegador sera executado em modo {mode}; credenciais: {secret_source()}.")
    print("Nenhuma senha sera exibida no console.\n")

    rows = CompassCollector(config, logger).collect()
    print(f"\nSUCESSO: login, acesso ao Starlink Fleet Usage e download do CSV concluido. Unidades lidas: {len(rows)}")


if __name__ == "__main__":
    main()
