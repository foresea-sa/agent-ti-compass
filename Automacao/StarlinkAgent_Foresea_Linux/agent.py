import json
import logging
from datetime import datetime
from pathlib import Path

from analytics.trends import apply_historical_analytics
from collectors.compass import CompassCollector
from collectors.starlink_api import StarlinkAPICollector
from database.db import get_history_by_units, init_db, insert_rows
from reports.executive_report import generate_excel, generate_pdf
from utils.periods import snapshot_key

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.json"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "starlink-agent.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("starlink-agent")


def load_config():
    if not CONFIG.exists():
        raise FileNotFoundError("config.json nao encontrado. Copie config.example.json para config.json e ajuste os valores.")
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def _forecast_text(row: dict) -> str:
    risk = str(row.get("forecast_risk") or "").upper()
    date_100 = row.get("forecast_limit_date")
    projected = float(row.get("projected_cycle_end_gb") or 0)
    quota = float(row.get("quota_gb") or 0)
    parts = []
    if risk in {"ESTOURADO", "ESTOURO PREVISTO"}:
        parts.append("Risco de franquia elevado")
    if date_100:
        try:
            dt = datetime.fromisoformat(str(date_100)).strftime("%d/%m/%Y")
        except Exception:
            dt = str(date_100)
        parts.append(f"100% estimado em {dt}")
    if projected and quota:
        parts.append(f"fim do ciclo: {projected/1000:.2f} TB / {quota/1000:.2f} TB")
    return ". ".join(parts) + ("." if parts else "")


def _recommended_action(row: dict) -> str:
    status = str(row.get("status") or "NORMAL").upper()
    risk = str(row.get("forecast_risk") or "CONTROLADO").upper()
    trend = str(row.get("trend") or "").upper()
    actions = {
        "NORMAL": "Manter monitoramento diario e preservar o perfil atual de uso.",
        "ATENCAO": "Revisar maiores consumidores e reduzir trafego nao essencial.",
        "CRITICO": "Aplicar controle de consumo, priorizar trafego operacional e acompanhar diariamente.",
        "EMERGENCIA": "Acionar controle imediato, identificar a origem do excesso e conter consumo nao prioritario.",
    }
    text = actions.get(status, actions["NORMAL"])
    if risk == "ESTOURO PREVISTO" and status not in {"EMERGENCIA"}:
        text += " A projecao indica estouro da franquia antes do fim do ciclo."
    elif risk == "RISCO ALTO":
        text += " A projecao esta proxima do limite contratado."
    if trend == "ACELERANDO":
        text += " O ritmo de consumo esta acelerando."
    return text


def enrich(rows, config):
    """Apply current-usage classification before historical forecasting."""
    thresholds = config["thresholds"]
    fallback_quotas = config["collection"].get("monthly_quota_gb", {})
    collected_at = datetime.now().isoformat(timespec="seconds")

    for r in rows:
        portal_quota = float(r.get("quota_gb") or 0)
        quota = portal_quota or float(fallback_quotas.get(r["unit"], 0))
        total = float(r.get("total_gb") or 0)
        overage = float(r.get("overage_gb") or 0)
        remaining_from_portal = float(r.get("remaining_gb") or 0)
        remaining = remaining_from_portal if remaining_from_portal > 0 else max(quota - total, 0)
        pct = (total / quota * 100) if quota else 0

        if overage > 0 or pct >= thresholds["emergency"]:
            status = "EMERGENCIA"
        elif pct >= thresholds["critical"]:
            status = "CRITICO"
        elif pct >= thresholds["warning"]:
            status = "ATENCAO"
        else:
            status = "NORMAL"

        r.update({
            "collected_at": collected_at,
            "quota_gb": quota,
            "remaining_gb": remaining,
            "usage_pct": pct,
            "status": status,
        })
        r["snapshot_key"] = snapshot_key(r)
    return rows


def build_email(rows, config=None):
    company = (config or {}).get("company", "Foresea")
    critical = [r for r in rows if r["status"] in {"CRITICO", "EMERGENCIA"}]
    attention = [r for r in rows if r["status"] == "ATENCAO"]
    normal = [r for r in rows if r["status"] == "NORMAL"]
    forecast_breach = [r for r in rows if r.get("forecast_risk") in {"ESTOURADO", "ESTOURO PREVISTO"}]
    total_overage = sum(float(r.get("overage_gb") or 0) for r in rows)
    projected_overage = sum(float(r.get("projected_overage_gb") or 0) for r in rows)
    total_usage = sum(float(r.get("total_gb") or 0) for r in rows)
    total_quota = sum(float(r.get("quota_gb") or 0) for r in rows)

    def status_style(status):
        return {
            "NORMAL": ("#E8F5E9", "#2E7D32"),
            "ATENCAO": ("#FFF8E1", "#8A6500"),
            "CRITICO": ("#FFF3E0", "#C45500"),
            "EMERGENCIA": ("#FFEBEE", "#B71C1C"),
        }.get(status, ("#F5F5F5", "#333333"))

    rows_html = []
    for r in sorted(rows, key=lambda x: x.get("usage_pct", 0), reverse=True):
        bg, fg = status_style(r["status"])
        forecast = _forecast_text(r)
        rows_html.append(
            "<tr>"
            f"<td style='padding:7px;border-bottom:1px solid #e5e7eb'><b>{r['unit']}</b></td>"
            f"<td style='padding:7px;text-align:right;border-bottom:1px solid #e5e7eb'>{r['total_gb']/1000:.2f} TB</td>"
            f"<td style='padding:7px;text-align:right;border-bottom:1px solid #e5e7eb'>{r['usage_pct']:.1f}%</td>"
            f"<td style='padding:7px;text-align:right;border-bottom:1px solid #e5e7eb'>{float(r.get('rate_gb_day') or 0):.1f} GB/dia</td>"
            f"<td style='padding:7px;border-bottom:1px solid #e5e7eb'>{r.get('trend','-')}</td>"
            f"<td style='padding:7px;text-align:center;background:{bg};color:{fg};font-weight:bold;border-bottom:1px solid #e5e7eb'>{r['status']}</td>"
            f"<td style='padding:7px;border-bottom:1px solid #e5e7eb'>{forecast}</td>"
            "</tr>"
        )

    return f"""
    <div style="font-family:Segoe UI,Arial,sans-serif;color:#263238;max-width:1100px">
      <div style="border-left:6px solid #00A7A7;padding-left:14px">
        <h2 style="margin:0;color:#1F2933">{company} | Relatorio Diario Starlink v0.8</h2>
        <p style="margin:6px 0 0 0;color:#60727C">Consumo atual, historico, velocidade e previsao de esgotamento da franquia.</p>
      </div>
      <p>Prezados,</p>
      <p>Segue o acompanhamento atualizado do consumo dos links Starlink.</p>
      <p style="color:#60727C;font-size:12px">Periodo Compass: {next((r.get('period') for r in rows if r.get('period')), '-')} | Dados: {next((r.get('data_freshness') for r in rows if r.get('data_freshness')), '-')}</p>
      <table style="border-collapse:separate;border-spacing:8px;width:100%;margin:12px 0">
        <tr>
          <td style="background:#EAF6F7;padding:12px;text-align:center"><b>{total_usage/1000:.2f} TB</b><br><span style="font-size:11px">Consumo total</span></td>
          <td style="background:#EAF6F7;padding:12px;text-align:center"><b>{total_quota/1000:.2f} TB</b><br><span style="font-size:11px">Franquia total</span></td>
          <td style="background:#FFF0F0;padding:12px;text-align:center"><b>{total_overage:.1f} GB</b><br><span style="font-size:11px">Overage atual</span></td>
          <td style="background:#FFF0F0;padding:12px;text-align:center"><b>{projected_overage:.1f} GB</b><br><span style="font-size:11px">Overage projetado</span></td>
          <td style="background:#FFF8E1;padding:12px;text-align:center"><b>{len(forecast_breach)}</b><br><span style="font-size:11px">Estouro atual/previsto</span></td>
          <td style="background:#EAF6F7;padding:12px;text-align:center"><b>{len(normal)} / {len(attention)} / {len(critical)}</b><br><span style="font-size:11px">Normal / Atencao / Critico+</span></td>
        </tr>
      </table>
      <table style="border-collapse:collapse;width:100%;font-size:12px">
        <thead><tr style="background:#1F2933;color:white">
          <th style="padding:8px;text-align:left">Sonda</th><th style="padding:8px">Consumo</th><th style="padding:8px">Uso</th>
          <th style="padding:8px">Ritmo</th><th style="padding:8px">Tendencia</th><th style="padding:8px">Status</th><th style="padding:8px;text-align:left">Previsao</th>
        </tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
      </table>
      <p>O PDF executivo e a planilha detalhada seguem em anexo.</p>
      <p>Atenciosamente,<br>Infraestrutura de TI<br>{company}</p>
    </div>
    """


def main():
    from mail.graph_mail import GraphMailer

    cfg = load_config()
    init_db()
    mode = cfg["collection"].get("mode", "compass")
    collector = CompassCollector(cfg, logger) if mode == "compass" else StarlinkAPICollector(cfg, logger)

    logger.info("Iniciando coleta - modo %s", mode)
    rows = collector.collect()
    rows = enrich(rows, cfg)

    lookback = int(cfg.get("history", {}).get("lookback_days", 90))
    history = get_history_by_units([r.get("unit") for r in rows], lookback_days=lookback)
    rows = apply_historical_analytics(rows, cfg, history)
    for r in rows:
        r["recommended_action"] = _recommended_action(r)

    insert_rows(rows)

    xlsx = generate_excel(rows, cfg)
    pdf = generate_pdf(rows, cfg)
    logger.info("Relatorios v0.8 gerados: %s | %s", xlsx, pdf)

    subject = f"{cfg['email']['subject_prefix']} | {datetime.now().strftime('%d/%m/%Y')}"
    body = build_email(rows, cfg)
    GraphMailer(cfg, logger).send(subject, body, [pdf, xlsx])
    logger.info("Processo concluido com sucesso")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Falha na execucao: %s", exc)
        raise
