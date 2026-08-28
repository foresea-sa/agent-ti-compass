from __future__ import annotations

import getpass

import keyring

SERVICE = "Foresea-Starlink-Agent"

print("Configuracao segura de credenciais - Foresea Starlink Agent v0.8 Windows")
print("Usuario e senha serao armazenados no Windows Credential Manager via keyring.")
print("A senha NAO sera gravada no config.json nem no codigo.\n")

compass_user = input("Usuario/e-mail do Compass: ").strip()
if not compass_user:
    raise SystemExit("Usuario do Compass e obrigatorio.")
compass_password = getpass.getpass("Senha do Compass: ")
if not compass_password:
    raise SystemExit("Senha do Compass e obrigatoria para o login automatico.")

keyring.set_password(SERVICE, "compass_username", compass_user)
keyring.set_password(SERVICE, "compass_password", compass_password)
print("\nCredenciais do Compass gravadas no Windows Credential Manager.")

print("\nOpcional: Microsoft Graph para envio automatico por e-mail.")
tenant_id = input("Tenant ID (ENTER para ignorar): ").strip()
if tenant_id:
    client_id = input("Client ID: ").strip()
    client_secret = getpass.getpass("Client Secret: ")
    if not client_id or not client_secret:
        raise SystemExit("Client ID e Client Secret sao obrigatorios quando Tenant ID e informado.")
    keyring.set_password(SERVICE, "graph_tenant_id", tenant_id)
    keyring.set_password(SERVICE, "graph_client_id", client_id)
    keyring.set_password(SERVICE, "graph_client_secret", client_secret)
    print("Credenciais Microsoft Graph gravadas no Windows Credential Manager.")

print("\nConfiguracao concluida.")
