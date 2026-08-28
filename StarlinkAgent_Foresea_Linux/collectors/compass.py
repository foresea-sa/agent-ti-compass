from __future__ import annotations

import json
import os
import re
import shutil
import time
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright

from utils.periods import file_sha256, parse_period
from utils.secrets import get_secret

BASE = Path(__file__).resolve().parents[1]
DATA_DIR = BASE / "data"
RAW_DIR = DATA_DIR / "raw"
DEBUG_DIR = BASE / "logs" / "debug"


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _as_number(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\xa0", " ")
    if not text:
        return 0.0
    multiplier = 1.0
    low = text.lower()
    if "tb" in low:
        multiplier = 1000.0
    elif "mb" in low:
        multiplier = 0.001
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text:
        return 0.0
    # Handles 1.234,56 and 1234.56.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) in (1, 2, 3):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        return float(text) * multiplier
    except ValueError:
        return 0.0


def _find_column(columns: dict[str, str], candidates: list[str], contains: bool = True) -> str | None:
    # columns is normalized_name -> original_name
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    if contains:
        for candidate in candidates:
            for normalized, original in columns.items():
                if candidate in normalized:
                    return original
    return None


class CompassCollector:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        DATA_DIR.mkdir(exist_ok=True)
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    def _username(self) -> str:
        username = get_secret("compass_username")
        if not username:
            raise RuntimeError(
                "Usuario do Compass nao configurado. No Windows execute credential_setup.py; "
                "no Linux configure /etc/starlink-agent/secrets.env."
            )
        return username

    def _password(self) -> str:
        password = get_secret("compass_password")
        if not password:
            raise RuntimeError(
                "Senha do Compass nao configurada. No Windows execute credential_setup.py; "
                "no Linux configure /etc/starlink-agent/secrets.env."
            )
        return password

    def _state_path(self) -> Path:
        cfg = self.config["compass"]
        raw = cfg.get("auth_state_file", "data/compass_state.json")
        path = Path(raw)
        return path if path.is_absolute() else BASE / path

    @staticmethod
    def _first_visible(locators):
        for locator in locators:
            try:
                if locator.count() and locator.first.is_visible():
                    return locator.first
            except Exception:
                continue
        return None

    def _is_login_like(self, page) -> bool:
        """Detecta tela/fluxo de autenticacao mesmo quando a URL nao contem /login."""
        try:
            url = (page.url or "").lower()
            if "/login" in url or "signin" in url or "sign-in" in url:
                return True
        except Exception:
            pass
        checks = [
            lambda: page.get_by_text("Welcome to Compass", exact=False),
            lambda: page.locator('input[type="password"]'),
            lambda: page.locator('input[type="email"]'),
            lambda: page.get_by_role("button", name=re.compile(r"^Login$|^Sign in$|^Entrar$", re.I)),
        ]
        for get_loc in checks:
            try:
                loc = get_loc()
                if loc.count() and loc.first.is_visible():
                    return True
            except Exception:
                continue
        return False

    def _is_authenticated(self, page) -> bool:
        """Confirma autenticacao por elementos do shell do Compass, nao apenas pela URL."""
        markers = [
            lambda: page.get_by_text("Map View", exact=False),
            lambda: page.get_by_text("Fleet", exact=True),
            lambda: page.get_by_text("Reports", exact=True),
            lambda: page.get_by_text("Manage My Services", exact=False),
            lambda: page.get_by_text("Starlink Fleet Usage", exact=False),
        ]
        hits = 0
        for get_loc in markers:
            try:
                loc = get_loc()
                if loc.count() and loc.first.is_visible():
                    hits += 1
            except Exception:
                continue
        return hits >= 1 and not self._is_login_like(page)

    def _login(self, page) -> None:
        cfg = self.config["compass"]
        login_url = cfg.get("login_url", "https://compass.speedcast.com/login")
        username = self._username()
        password = self._password()
        timeout_ms = int(cfg.get("timeout_ms", 60000))
        wait_seconds = int(cfg.get("login_wait_seconds", 60))

        self.logger.info("Sessao ausente/expirada. Efetuando login automatico no Compass.")
        page.goto(login_url, wait_until="domcontentloaded")
        page.set_default_timeout(timeout_ms)

        user_field = self._first_visible([
            page.get_by_label(re.compile(r"Email Address|E-mail|Email|Usuario|User", re.I)),
            page.locator('input[type="email"]'),
            page.locator('input[name*="email" i]'),
            page.locator('input[name*="user" i]'),
            page.locator('input[type="text"]'),
        ])
        if user_field is None:
            diag = self._diagnostic_snapshot(page, "login_user_not_found")
            raise RuntimeError(f"Campo de usuario/e-mail nao encontrado. Diagnostico: {diag}")
        user_field.fill(username)

        first_submit = self._first_visible([
            page.get_by_role("button", name=re.compile(r"^Login$|^Continue$|^Next$", re.I)),
            page.locator('button[type="submit"]'),
            page.locator('input[type="submit"]'),
        ])
        if first_submit is not None:
            first_submit.click()
        else:
            user_field.press("Enter")

        # O Compass pode trocar de rota/dominio entre e-mail e senha. Nao trate
        # uma simples mudanca de URL como login concluido: espere senha OU shell autenticado.
        password_field = None
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            password_field = self._first_visible([
                page.get_by_label(re.compile(r"Password|Senha", re.I)),
                page.locator('input[type="password"]'),
                page.locator('input[name*="password" i]'),
            ])
            if password_field is not None:
                break
            if self._is_authenticated(page):
                self.logger.info("Login concluido sem solicitar senha nesta execucao.")
                return
            time.sleep(0.5)

        if password_field is None:
            diag = self._diagnostic_snapshot(page, "password_not_found")
            raise RuntimeError(f"Campo de senha nao apareceu apos o envio do usuario. Diagnostico: {diag}")

        password_field.fill(password)
        second_submit = self._first_visible([
            page.get_by_role("button", name=re.compile(r"^Login$|^Sign in$|^Entrar$|^Continue$|^Submit$", re.I)),
            page.locator('button[type="submit"]'),
            page.locator('input[type="submit"]'),
        ])
        if second_submit is None:
            password_field.press("Enter")
        else:
            second_submit.click()

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._is_authenticated(page):
                self.logger.info("Login automatico no Compass concluido.")
                return
            # Se a senha continuar visivel, procure mensagem de erro cedo.
            try:
                body = page.locator("body").inner_text(timeout=1000)
                if re.search(r"invalid|incorrect|incorret|failed|falhou|locked|bloquead", body, re.I):
                    diag = self._diagnostic_snapshot(page, "login_rejected")
                    raise RuntimeError(f"O portal indicou falha de autenticacao. Diagnostico: {diag}")
            except RuntimeError:
                raise
            except Exception:
                pass
            time.sleep(0.5)

        diag = self._diagnostic_snapshot(page, "login_timeout")
        raise RuntimeError(f"Login nao foi confirmado dentro do tempo limite. Diagnostico: {diag}")

    def _failure_screenshot(self, page, name: str) -> Path:
        return Path(self._diagnostic_snapshot(page, name)["screenshot"])

    def _diagnostic_snapshot(self, page, name: str) -> dict:
        """Salva screenshot, HTML e resumo textual sem expor senhas."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shot = DEBUG_DIR / f"{stamp}_{name}.png"
        html = DEBUG_DIR / f"{stamp}_{name}.html"
        meta = DEBUG_DIR / f"{stamp}_{name}.json"
        body_excerpt = ""
        title = ""
        url = ""
        try:
            url = page.url
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            pass
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:
            pass
        try:
            content = page.content()
            # Remove valores de password de inputs antes de gravar HTML.
            content = re.sub(r'(<input[^>]*type=["\']password["\'][^>]*value=)["\'][^"\']*["\']', r'\1"***"', content, flags=re.I)
            html.write_text(content, encoding="utf-8")
        except Exception:
            pass
        try:
            body_excerpt = page.locator("body").inner_text(timeout=3000)[:4000]
        except Exception:
            pass
        data = {
            "timestamp": stamp,
            "url": url,
            "title": title,
            "login_like": self._is_login_like(page),
            "authenticated_marker": self._is_authenticated(page),
            "body_excerpt": body_excerpt,
            "screenshot": str(shot),
            "html": str(html),
        }
        try:
            meta.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        self.logger.error("Diagnostico Compass: url=%s title=%s screenshot=%s meta=%s", url, title, shot, meta)
        return {**data, "meta": str(meta)}

    def _session_expired(self, page) -> bool:
        return self._is_login_like(page)

    def _period_label(self, page) -> str:
        # The Compass screen shows the date range in an input next to the calendar icon.
        try:
            values = page.locator("input").evaluate_all(
                "els => els.map(e => e.value || e.getAttribute('value') || '').filter(Boolean)"
            )
            for value in values:
                if re.search(r"\d{4}[-/]\d{2}[-/]\d{2}.*\d{4}[-/]\d{2}[-/]\d{2}", value):
                    return value.strip()
        except Exception:
            pass
        return ""

    def _download_csv(self, page) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        selectors = [
            lambda: page.get_by_role("button", name=re.compile(r"^CSV$", re.I)),
            lambda: page.get_by_text("CSV", exact=True),
            lambda: page.locator("button").filter(has_text=re.compile(r"CSV", re.I)),
        ]
        last_error = None
        for get_locator in selectors:
            try:
                locator = get_locator()
                if locator.count() < 1:
                    continue
                with page.expect_download(timeout=self.config["compass"].get("timeout_ms", 60000)) as info:
                    locator.first.click()
                download = info.value
                suggested = str(download.suggested_filename or "starlink_fleet_usage.csv")
                safe = re.sub(r"[^A-Za-z0-9._-]+", "_", suggested).strip("_") or "starlink_fleet_usage.csv"
                destination = RAW_DIR / f"{stamp}_{safe}"
                download.save_as(str(destination))
                self.logger.info("CSV do Compass salvo em %s (nome portal: %s)", destination, suggested)
                return destination
            except Exception as exc:
                last_error = exc

        shot = self._failure_screenshot(page, "csv_button_not_found")
        raise RuntimeError(
            f"Nao foi possivel acionar o botao CSV do Compass. Screenshot: {shot}. Erro: {last_error}"
        )

    def _load_csv(self, path: Path) -> pd.DataFrame:
        attempts = [
            {"encoding": "utf-8-sig", "sep": None, "engine": "python"},
            {"encoding": "utf-8", "sep": None, "engine": "python"},
            {"encoding": "latin-1", "sep": None, "engine": "python"},
        ]
        last_error = None
        for kwargs in attempts:
            try:
                df = pd.read_csv(path, **kwargs)
                if len(df.columns) > 1:
                    return df
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Nao foi possivel interpretar o CSV exportado pelo Compass: {last_error}")

    def _unit_from_name(self, raw_name: str) -> str | None:
        aliases = self.config["collection"].get("unit_aliases", {})
        normalized_name = _norm(raw_name)
        alias_patterns = []
        for unit, patterns in aliases.items():
            for pattern in patterns:
                alias_patterns.append((unit, _norm(pattern)))
        # Longest patterns first avoids cases such as Norbe VI matching Norbe VIII.
        for unit, pattern in sorted(alias_patterns, key=lambda x: len(x[1]), reverse=True):
            if re.search(r"(?:^|_)" + re.escape(pattern) + r"(?:_|$)", normalized_name):
                return unit

        # Safe automatic fallbacks for names visible on the Compass fleet screen.
        rules = [
            ("ODN1", ["odn_1", "odn1"]),
            ("ODN2", ["odn_2", "odn2"]),
            ("N06", ["norbe_vi", "norbe_6"]),
            ("N08", ["norbe_viii", "norbe_8"]),
            ("N09", ["norbe_ix", "norbe_9"]),
            ("HTQ", ["hunter_queen"]),
            ("MACAE", ["base_macae"]),
            ("RIO", ["base_rio"]),
        ]
        for unit, patterns in rules:
            for pattern in sorted(patterns, key=len, reverse=True):
                if re.search(r"(?:^|_)" + re.escape(pattern) + r"(?:_|$)", normalized_name):
                    return unit
        return None

    def _parse_csv(self, path: Path, period: str) -> list[dict]:
        """Parse the CSV exported by Compass Starlink Fleet Usage.

        v0.4 is validated against the Speedcast schema observed on 27/08/2026:
        Kit Name, Kit ID(s), Business Service, Service Line, Plan Name, Limit (GB),
        Data Boosters (GB), Priority (GB), Standard (GB), % of Limit.
        """
        df = self._load_csv(path)
        period_meta = parse_period(period, path.name)
        period = period_meta.get("period_label") or period or ""
        source_hash = file_sha256(path)
        original_columns = [str(c).strip() for c in df.columns]
        columns = {_norm(c): c for c in original_columns}
        self.logger.info("Colunas do CSV Compass: %s", ", ".join(original_columns))

        # Business Service is the human-readable vessel/site name in the real Compass CSV.
        # Keep the generic fallbacks for future Speedcast schema changes.
        name_col = _find_column(columns, [
            "business_service", "business_service_name", "site_name", "fleet_name",
            "terminal_name", "description", "name", "site", "terminal", "service"
        ])
        kit_name_col = _find_column(columns, ["kit_name", "terminal_name", "starlink_kit_name"], contains=False)
        kit_id_col = _find_column(columns, ["kit_id_s", "kit_ids", "kit_id", "terminal_id"], contains=False)
        service_line_col = _find_column(columns, ["service_line", "service_line_id"], contains=False)
        plan_col = _find_column(columns, ["plan_name", "plan"], contains=False)

        priority_col = _find_column(columns, ["priority_gb", "priority", "priority_data"])
        booster_col = _find_column(columns, ["data_boosters_gb", "data_boosters", "data_booster", "boosters", "booster"])
        standard_col = _find_column(columns, ["standard_gb", "standard", "standard_data"])
        remaining_col = _find_column(columns, ["remaining_gb", "remaining", "remaining_data"])
        overage_col = _find_column(columns, ["overage_gb", "overage", "overage_data"])
        quota_col = _find_column(columns, ["limit_gb", "quota", "allowance", "data_limit", "plan_limit"])
        total_col = _find_column(columns, ["total_usage", "usage_total", "data_usage", "used_data", "used_gb", "usage_gb", "total"])
        pct_col = _find_column(columns, ["of_limit", "percent_of_limit", "usage_pct", "usage_percent"], contains=False)

        if not name_col:
            archive = path.with_suffix(".unparsed.csv")
            shutil.copy2(path, archive)
            raise RuntimeError(
                "O CSV foi baixado, mas nao consegui identificar a coluna Business Service/nome da unidade. "
                f"Colunas recebidas: {original_columns}. Arquivo preservado em {archive}."
            )

        numeric_detected = any([priority_col, booster_col, standard_col, remaining_col, overage_col, quota_col, total_col])
        if not numeric_detected:
            raise RuntimeError(
                "O CSV foi baixado, mas nenhuma coluna de consumo foi reconhecida. "
                f"Colunas recebidas: {original_columns}."
            )

        wanted_units = set(self.config["collection"].get("units", []))
        rows: list[dict] = []
        for _, item in df.iterrows():
            raw_name = str(item.get(name_col, "")).strip()
            if not raw_name or raw_name.lower() == "nan":
                continue
            unit = self._unit_from_name(raw_name)
            if not unit or (wanted_units and unit not in wanted_units):
                continue

            priority = _as_number(item.get(priority_col)) if priority_col else 0.0
            booster = _as_number(item.get(booster_col)) if booster_col else 0.0
            standard = _as_number(item.get(standard_col)) if standard_col else 0.0
            quota = _as_number(item.get(quota_col)) if quota_col else 0.0

            # In the validated Compass export, consumed data is the sum of Priority,
            # Data Boosters and Standard. Overage is not a separate consumption bucket;
            # it is the portion above the plan limit.
            total = _as_number(item.get(total_col)) if total_col else (priority + booster + standard)
            explicit_overage = _as_number(item.get(overage_col)) if overage_col else None
            explicit_remaining = _as_number(item.get(remaining_col)) if remaining_col else None
            overage = explicit_overage if explicit_overage is not None else max(total - quota, 0.0)
            remaining = explicit_remaining if explicit_remaining is not None else max(quota - total, 0.0)

            portal_pct = _as_number(item.get(pct_col)) if pct_col else 0.0
            if quota <= 0 and (total > 0 or remaining > 0):
                quota = max(total - overage + remaining, 0.0)
            if portal_pct <= 0 and quota > 0:
                portal_pct = total / quota * 100.0

            plan_name = str(item.get(plan_col, "") or "").strip() if plan_col else ""
            service_line = str(item.get(service_line_col, "") or "").strip() if service_line_col else ""
            terminal = str(item.get(kit_id_col, "") or "").strip() if kit_id_col else ""
            kit_name = str(item.get(kit_name_col, "") or "").strip() if kit_name_col else ""
            if terminal.lower() == "nan":
                terminal = ""
            if service_line.lower() == "nan":
                service_line = ""
            if plan_name.lower() == "nan":
                plan_name = ""
            if kit_name.lower() == "nan":
                kit_name = ""

            # The real CSV can contain deactivated/placeholder service lines with no plan
            # and zero usage (seen for Norbe IX). They should not pollute the unit summary.
            if quota <= 0 and total <= 0 and not plan_name:
                self.logger.debug("Ignorando linha sem plano/consumo: %s | %s", raw_name, service_line)
                continue

            rows.append({
                "unit": unit,
                "source_name": raw_name,
                "terminal": terminal,
                "kit_name": kit_name,
                "service_line": service_line,
                "plan_name": plan_name,
                "period": period,
                "period_start": period_meta.get("period_start"),
                "period_end": period_meta.get("period_end"),
                "period_days": period_meta.get("period_days"),
                "source_file": path.name,
                "source_sha256": source_hash,
                "priority_gb": priority,
                "booster_gb": booster,
                "standard_gb": standard,
                "overage_gb": overage,
                "remaining_gb": remaining,
                "total_gb": total,
                "quota_gb": quota,
                "portal_usage_pct": portal_pct,
            })

        if not rows:
            raise RuntimeError(
                "O CSV foi interpretado, mas nenhuma unidade configurada foi encontrada. "
                "Revise collection.unit_aliases no config.json."
            )

        if self.config["collection"].get("aggregate_by_unit", True):
            rows = self._aggregate(rows)
        return rows

    def _aggregate(self, rows: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = {}
        for row in rows:
            unit = row["unit"]
            if unit not in grouped:
                grouped[unit] = {
                    "unit": unit,
                    "source_name": row["source_name"],
                    "terminal": row.get("terminal", ""),
                    "kit_name": row.get("kit_name", ""),
                    "service_line": row.get("service_line", ""),
                    "plan_name": row.get("plan_name", ""),
                    "period": row.get("period", ""),
                    "period_start": row.get("period_start"),
                    "period_end": row.get("period_end"),
                    "period_days": row.get("period_days"),
                    "source_file": row.get("source_file", ""),
                    "source_sha256": row.get("source_sha256", ""),
                    "priority_gb": 0.0,
                    "booster_gb": 0.0,
                    "standard_gb": 0.0,
                    "overage_gb": 0.0,
                    "remaining_gb": 0.0,
                    "total_gb": 0.0,
                    "quota_gb": 0.0,
                    "portal_usage_pct": 0.0,
                }
            g = grouped[unit]
            for field in ["priority_gb", "booster_gb", "standard_gb", "overage_gb", "remaining_gb", "total_gb", "quota_gb"]:
                g[field] += float(row.get(field, 0) or 0)

            for field in ["terminal", "kit_name", "service_line", "plan_name"]:
                value = str(row.get(field, "") or "").strip()
                existing = str(g.get(field, "") or "")
                if value and value not in existing.split(" | "):
                    g[field] = " | ".join(filter(None, [existing, value]))
            if row["source_name"] not in g["source_name"].split(" | "):
                g["source_name"] += " | " + row["source_name"]

        for g in grouped.values():
            quota = float(g.get("quota_gb") or 0)
            total = float(g.get("total_gb") or 0)
            g["portal_usage_pct"] = (total / quota * 100.0) if quota > 0 else 0.0
            # Recalculate consolidated balance to avoid summing stale derived values.
            g["overage_gb"] = max(total - quota, 0.0) if quota > 0 else float(g.get("overage_gb") or 0)
            g["remaining_gb"] = max(quota - total, 0.0) if quota > 0 else float(g.get("remaining_gb") or 0)
        return list(grouped.values())

    def collect(self):
        cfg = self.config["compass"]
        state_path = self._state_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)

        reset_session = os.environ.get("STARLINK_RESET_SESSION", "").strip().lower() in {"1", "true", "yes", "sim"}
        if reset_session and state_path.exists():
            try:
                state_path.unlink()
                self.logger.info("Sessao salva removida para teste de autenticacao limpa: %s", state_path)
            except Exception as exc:
                self.logger.warning("Nao foi possivel remover sessao salva %s: %s", state_path, exc)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=cfg.get("headless", True))
            context_args = {"accept_downloads": True}
            if cfg.get("use_saved_session", True) and state_path.exists() and not reset_session:
                context_args["storage_state"] = str(state_path)
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.set_default_timeout(cfg.get("timeout_ms", 60000))
            usage_url = cfg.get("usage_url", "https://compass.speedcast.com/fleet/starlinkusage")
            self.logger.info("Abrindo Starlink Fleet Usage: %s", usage_url)
            page.goto(usage_url, wait_until="domcontentloaded")

            if self._session_expired(page):
                if not cfg.get("auto_login", True):
                    shot = self._failure_screenshot(page, "session_expired")
                    browser.close()
                    raise RuntimeError(
                        "A sessao do Compass expirou e auto_login esta desabilitado. "
                        f"Screenshot salvo em {shot}."
                    )
                try:
                    self._login(page)
                    page.goto(usage_url, wait_until="domcontentloaded")
                except Exception as exc:
                    shot = self._failure_screenshot(page, "automatic_login_failed")
                    browser.close()
                    raise RuntimeError(f"Falha no login automatico do Compass. Screenshot: {shot}. Erro: {exc}")

            # A pagina pode renderizar o titulo de formas diferentes. Considere pronta
            # quando o titulo OU o botao CSV estiver visivel.
            deadline = time.time() + (int(cfg.get("timeout_ms", 60000)) / 1000.0)
            ready = False
            while time.time() < deadline:
                if self._session_expired(page):
                    # Redirecionamento tardio para login: tente autenticar uma vez.
                    try:
                        self._login(page)
                        page.goto(usage_url, wait_until="domcontentloaded")
                    except Exception as exc:
                        diag = self._diagnostic_snapshot(page, "late_login_failed")
                        browser.close()
                        raise RuntimeError(f"Falha de autenticacao durante abertura do relatorio. Diagnostico: {diag}. Erro: {exc}")
                markers = [
                    page.get_by_text("Starlink Fleet Usage", exact=False),
                    page.get_by_role("button", name=re.compile(r"^CSV$", re.I)),
                    page.get_by_text("CSV", exact=True),
                ]
                for marker in markers:
                    try:
                        if marker.count() and marker.first.is_visible():
                            ready = True
                            break
                    except Exception:
                        continue
                if ready:
                    break
                time.sleep(0.5)

            if not ready:
                diag = self._diagnostic_snapshot(page, "usage_page_not_ready")
                browser.close()
                raise RuntimeError(
                    "Pagina Starlink Fleet Usage nao ficou pronta. "
                    f"URL final: {diag.get('url')}. Titulo: {diag.get('title')}. "
                    f"Diagnostico: {diag.get('meta')} | Screenshot: {diag.get('screenshot')}"
                )

            period = self._period_label(page)
            csv_path = self._download_csv(page)
            # Mantem cookies/tokens para acelerar a proxima execucao. Se expirarem, o agente reloga sozinho.
            context.storage_state(path=str(state_path))
            browser.close()

        rows = self._parse_csv(csv_path, period)
        self.logger.info("Coleta Compass concluida: %d unidade(s)", len(rows))
        return rows
