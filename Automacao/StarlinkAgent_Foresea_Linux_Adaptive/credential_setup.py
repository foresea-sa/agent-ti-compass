#!/usr/bin/env python3
from __future__ import annotations

import getpass
import os
import pwd
from pathlib import Path

SECRETS_PATH = Path(os.environ.get("STARLINK_SECRETS_PATH", "/etc/starlink-agent/secrets.env"))
SERVICE_USER = os.environ.get("SERVICE_USER", "starlinkagent")


def quote(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "")
    return f'"{value}"'


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Execute como root: sudo ./credential_setup.py")

    print("Configuracao segura - Foresea Starlink Agent v0.8 Linux")
    print(f"Segredos serao gravados em {SECRETS_PATH} com permissao 600 root:root.")
    print("O arquivo nao fica dentro da pasta do projeto.\n")

    user = input("Usuario/e-mail do Compass: ").strip()
    if not user:
        raise SystemExit("Usuario do Compass e obrigatorio.")
    password = getpass.getpass("Senha do Compass: ")
    if not password:
        raise SystemExit("Senha do Compass e obrigatoria.")

    values = {
        "STARLINK_COMPASS_USERNAME": user,
        "STARLINK_COMPASS_PASSWORD": password,
    }

    print("\nOpcional: Microsoft Graph para envio automatico.")
    tenant = input("Tenant ID (ENTER para ignorar): ").strip()
    if tenant:
        client_id = input("Client ID: ").strip()
        client_secret = getpass.getpass("Client Secret: ")
        if not client_id or not client_secret:
            raise SystemExit("Client ID e Client Secret sao obrigatorios quando Tenant ID e informado.")
        values.update({
            "STARLINK_GRAPH_TENANT_ID": tenant,
            "STARLINK_GRAPH_CLIENT_ID": client_id,
            "STARLINK_GRAPH_CLIENT_SECRET": client_secret,
        })

    SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    body = "# Foresea Starlink Agent v0.8 - gerado por credential_setup.py\n"
    body += "\n".join(f"{k}={quote(v)}" for k, v in values.items()) + "\n"
    fd = os.open(SECRETS_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        account = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        account = None
        print(f"AVISO: usuario de servico {SERVICE_USER} ainda nao existe; execute install-linux.sh primeiro.")
    if account is not None:
        os.chown(SECRETS_PATH, 0, account.pw_gid)
        os.chmod(SECRETS_PATH, 0o640)
    else:
        os.chmod(SECRETS_PATH, 0o600)

    print("\nSegredos configurados. Nenhuma senha foi gravada em config.json ou no codigo.")


if __name__ == "__main__":
    main()
