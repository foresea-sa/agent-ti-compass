import base64
import msal
import requests

from utils.secrets import get_secret

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

class GraphMailer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def _token(self):
        tenant = get_secret("graph_tenant_id")
        client_id = get_secret("graph_client_id")
        secret = get_secret("graph_client_secret")
        if not all([tenant, client_id, secret]):
            raise RuntimeError("Credenciais Microsoft Graph nao configuradas no mecanismo seguro da plataforma.")
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=secret,
        )
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(f"Falha ao obter token Graph: {result.get('error_description', result)}")
        return result["access_token"]

    def send(self, subject, body, attachments):
        cfg = self.config["email"]
        if not cfg.get("enabled", False):
            self.logger.info("Envio de e-mail desabilitado no config.json")
            return

        token = self._token()
        att = []
        for path in attachments:
            with open(path, "rb") as f:
                content = base64.b64encode(f.read()).decode("ascii")
            att.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentBytes": content,
            })

        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body},
                "toRecipients": [{"emailAddress": {"address": x}} for x in cfg["recipients"]],
                "attachments": att,
            },
            "saveToSentItems": True,
        }
        sender = cfg["sender"]
        url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=60)
        if resp.status_code not in (202,):
            raise RuntimeError(f"Falha no envio do e-mail: HTTP {resp.status_code} - {resp.text}")
        self.logger.info("E-mail enviado para %s", "; ".join(cfg["recipients"]))
