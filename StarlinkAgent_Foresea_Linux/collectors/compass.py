from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
VIDEO_DIR = DEBUG_DIR / "videos"
VIDEO_RAW_DIR = VIDEO_DIR / "raw"
AGENT_BUILD = "0.8.8"


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
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        VIDEO_RAW_DIR.mkdir(parents=True, exist_ok=True)

    def _video_enabled(self) -> bool:
        env = os.environ.get("STARLINK_RECORD_VIDEO", "").strip().lower()
        if env in {"1", "true", "yes", "sim", "on"}:
            return True
        if env in {"0", "false", "no", "nao", "off"}:
            return False
        return bool(self.config.get("compass", {}).get("record_activity_video", False))

    def _video_context_args(self) -> dict:
        if not self._video_enabled():
            return {}
        cfg = self.config.get("compass", {})
        width = max(320, int(cfg.get("video_width", 960)))
        height = max(240, int(cfg.get("video_height", 540)))
        VIDEO_RAW_DIR.mkdir(parents=True, exist_ok=True)
        self.logger.info("Gravacao de atividade habilitada: %sx%s (MP4 sera gerado ao final).", width, height)
        return {
            "record_video_dir": str(VIDEO_RAW_DIR),
            "record_video_size": {"width": width, "height": height},
            "viewport": {"width": width, "height": height},
        }

    def _finalize_browser_run(self, context, page, browser, status: str) -> str | None:
        """Fecha o contexto e converte a gravacao Playwright (WebM) para MP4 compacto.

        O Playwright finaliza o arquivo de video apenas ao fechar o BrowserContext.
        Por isso esta rotina deve ser usada em vez de browser.close() durante a coleta.
        """
        video = None
        if self._video_enabled():
            try:
                video = page.video
            except Exception:
                video = None

        try:
            context.close()
        except Exception as exc:
            self.logger.warning("Falha ao fechar contexto do navegador: %s", exc)
        try:
            browser.close()
        except Exception:
            pass

        if video is None:
            return None

        try:
            raw_path = Path(video.path())
        except Exception as exc:
            self.logger.warning("Nao foi possivel localizar o video bruto do Playwright: %s", exc)
            return None

        if not raw_path.exists():
            self.logger.warning("Video bruto informado pelo Playwright nao existe: %s", raw_path)
            return None

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_status = re.sub(r"[^A-Za-z0-9_-]+", "_", status or "run")
        mp4_path = VIDEO_DIR / f"{stamp}_compass_{safe_status}.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            fallback = VIDEO_DIR / f"{stamp}_compass_{safe_status}.webm"
            try:
                shutil.move(str(raw_path), str(fallback))
            except Exception:
                fallback = raw_path
            self.logger.warning("ffmpeg nao encontrado; video mantido em WebM: %s", fallback)
            return str(fallback)

        cfg = self.config.get("compass", {})
        fps = max(5, int(cfg.get("video_fps", 10)))
        crf = min(38, max(18, int(cfg.get("video_crf", 30))))
        width = max(320, int(cfg.get("video_width", 960)))
        vf = f"fps={fps},scale={width}:-2"
        cmd = [
            ffmpeg, "-y", "-loglevel", "error", "-i", str(raw_path),
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
            "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(mp4_path),
        ]
        try:
            subprocess.run(cmd, check=True, timeout=180)
            try:
                raw_path.unlink()
            except Exception:
                pass
            self.logger.info("Mini MP4 da atividade salvo em: %s", mp4_path)
            return str(mp4_path)
        except Exception as exc:
            fallback = VIDEO_DIR / f"{stamp}_compass_{safe_status}.webm"
            try:
                shutil.move(str(raw_path), str(fallback))
            except Exception:
                fallback = raw_path
            self.logger.warning("Falha ao converter video para MP4 (%s). WebM preservado em: %s", exc, fallback)
            return str(fallback)

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

    def _login_user_field_in_context(self, context):
        """Procura o campo de usuario/e-mail em uma Page ou Frame."""
        try:
            return self._first_visible([
                context.get_by_label(re.compile(r"Email Address|E-mail|Email|Usuario|User", re.I)),
                context.locator('input[type="email"]'),
                context.locator('input[name*="email" i]'),
                context.locator('input[name*="user" i]'),
                context.locator('input[autocomplete="username"]'),
                context.locator('input[type="text"]'),
            ])
        except Exception:
            return None

    def _wait_for_login_user_field(self, page):
        """Aguarda a SPA de login do Compass renderizar de verdade.

        Em alguns ambientes o /login devolve rapidamente o shell HTML com titulo
        "Compass", mas o formulario React/Angular leva dezenas de segundos para
        aparecer. O agente nao deve interpretar esse estado intermediario como
        mudanca de layout. Tambem procuramos em iframes para tolerar futuras
        alteracoes do portal.
        """
        cfg = self.config["compass"]
        timeout_ms = int(cfg.get("login_page_timeout_ms", 90000))
        poll_ms = max(250, int(cfg.get("login_poll_interval_ms", 1000)))
        deadline = time.time() + (timeout_ms / 1000.0)
        next_log = time.time() + 10

        while time.time() < deadline:
            field = self._login_user_field_in_context(page)
            if field is not None:
                return field

            # O portal pode futuramente encapsular o formulario em iframe.
            try:
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    field = self._login_user_field_in_context(frame)
                    if field is not None:
                        self.logger.info("Campo de usuario encontrado em iframe: %s", frame.url or "(sem URL)")
                        return field
            except Exception:
                pass

            # Se o portal terminou de autenticar por sessao/cookie enquanto esperavamos,
            # nao faz sentido continuar procurando o formulario.
            if self._is_authenticated(page):
                return None

            if time.time() >= next_log:
                elapsed = int((timeout_ms / 1000.0) - max(0, deadline - time.time()))
                try:
                    ready_state = page.evaluate("document.readyState")
                except Exception:
                    ready_state = "unknown"
                try:
                    inputs = page.locator("input").count()
                except Exception:
                    inputs = -1
                try:
                    frames = len(page.frames)
                except Exception:
                    frames = -1
                self.logger.info(
                    "Aguardando renderizacao do formulario de login do Compass (%ss decorridos; readyState=%s; inputs=%s; frames=%s).",
                    elapsed, ready_state, inputs, frames,
                )
                next_log = time.time() + 10

            time.sleep(poll_ms / 1000.0)

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

    def _wait_post_login_stabilization(self, page) -> None:
        """Aguarda o shell do Compass terminar a inicializacao apos autenticar.

        O portal pode exibir o menu superior antes de terminar o bootstrap da SPA.
        Navegar para /fleet/starlinkusage durante esse intervalo pode deixar a pagina
        presa no spinner. Por isso existe uma janela minima de estabilizacao apos o
        login confirmado.
        """
        cfg = self.config["compass"]
        min_seconds = max(0, int(cfg.get("post_login_stabilization_seconds", 70)))
        max_seconds = max(min_seconds, int(cfg.get("post_login_max_wait_seconds", 120)))

        self.logger.info(
            "Login aceito. Aguardando estabilizacao completa do portal Compass por pelo menos %ss antes de abrir relatórios.",
            min_seconds,
        )

        started = time.time()
        next_log = started + 15
        while True:
            elapsed = time.time() - started

            if self._is_login_like(page):
                diag = self._diagnostic_snapshot(page, "session_returned_to_login_during_stabilization")
                raise RuntimeError(
                    f"A sessao retornou para o fluxo de login durante a estabilizacao. Diagnostico: {diag}"
                )

            # Aguardar sempre a janela minima. Os menus do Compass aparecem antes
            # do fim do bootstrap, portanto eles nao sao suficientes para liberar a navegacao.
            if elapsed >= min_seconds:
                break

            if time.time() >= next_log:
                remaining = max(0, int(min_seconds - elapsed))
                self.logger.info("Compass ainda em estabilizacao pos-login (%ss restantes aprox.).", remaining)
                next_log = time.time() + 15

            time.sleep(1)

        # Depois da janela minima, aguarde brevemente um estado de carga mais calmo.
        # Algumas SPAs mantem conexoes abertas, portanto networkidle e apenas best-effort.
        remaining_budget_ms = max(1000, int((max_seconds - (time.time() - started)) * 1000))
        try:
            page.wait_for_load_state("networkidle", timeout=min(15000, remaining_budget_ms))
        except Exception:
            pass

        if not self._is_authenticated(page):
            # O shell pode momentaneamente nao expor os mesmos marcadores, mas nao
            # devemos seguir se voltamos ao login.
            if self._is_login_like(page):
                diag = self._diagnostic_snapshot(page, "portal_not_authenticated_after_stabilization")
                raise RuntimeError(f"Portal nao permaneceu autenticado apos estabilizacao. Diagnostico: {diag}")

        self.logger.info("Portal Compass estabilizado. Navegacao para Starlink Fleet Usage liberada.")

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

        login_page_timeout_ms = int(cfg.get("login_page_timeout_ms", 90000))
        self.logger.info(
            "URL de login carregada. Aguardando o formulario do Compass renderizar (timeout=%ss).",
            int(login_page_timeout_ms / 1000),
        )
        user_field = self._wait_for_login_user_field(page)
        if user_field is None:
            # Pode ter ocorrido login por sessao existente durante a espera.
            if self._is_authenticated(page):
                self.logger.info("Sessao autenticada detectada durante a espera do formulario de login.")
                self._wait_post_login_stabilization(page)
                return
            diag = self._diagnostic_snapshot(page, "login_user_not_found")
            raise RuntimeError(
                "Campo de usuario/e-mail nao apareceu dentro do tempo de renderizacao do login. "
                f"Diagnostico: {diag}"
            )
        self.logger.info("Formulario de login do Compass renderizado. Preenchendo usuario.")
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
                self._wait_post_login_stabilization(page)
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
                self._wait_post_login_stabilization(page)
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
        try:
            ready_state = page.evaluate("document.readyState")
        except Exception:
            ready_state = "unknown"
        try:
            input_count = page.locator("input").count()
        except Exception:
            input_count = -1
        try:
            iframe_count = page.locator("iframe").count()
        except Exception:
            iframe_count = -1
        try:
            frame_urls = [f.url for f in page.frames][:20]
        except Exception:
            frame_urls = []
        try:
            html_size = len(page.content())
        except Exception:
            html_size = -1

        data = {
            "timestamp": stamp,
            "url": url,
            "title": title,
            "ready_state": ready_state,
            "login_like": self._is_login_like(page),
            "authenticated_marker": self._is_authenticated(page),
            "input_count": input_count,
            "iframe_count": iframe_count,
            "frame_urls": frame_urls,
            "html_size": html_size,
            "body_text_length": len(body_excerpt),
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

    def _csv_locator(self, page):
        """Retorna o botao/link CSV visivel da pagina de Starlink Fleet Usage."""
        candidates = [
            page.get_by_role("button", name=re.compile(r"^CSV$", re.I)),
            page.get_by_role("link", name=re.compile(r"^CSV$", re.I)),
            page.locator("button").filter(has_text=re.compile(r"^\s*CSV\s*$", re.I)),
            page.locator("a").filter(has_text=re.compile(r"^\s*CSV\s*$", re.I)),
            page.get_by_text("CSV", exact=True),
        ]
        return self._first_visible(candidates)

    def _wait_for_usage_page_ready(self, page):
        """Aguarda a tela real de Starlink Fleet Usage ficar pronta.

        Nao usa apenas o texto 'Starlink Fleet Usage' como marcador porque o texto
        pode existir no menu/dropdown antes de o relatorio terminar de renderizar.
        O criterio principal e o botao CSV visivel, que e exatamente o recurso que
        sera usado pela coleta.
        """
        cfg = self.config["compass"]
        timeout_ms = int(cfg.get("usage_page_timeout_ms", 120000))
        poll_ms = max(250, int(cfg.get("usage_poll_interval_ms", 1000)))
        deadline = time.time() + timeout_ms / 1000.0
        next_log = time.time() + 10
        started = time.time()

        self.logger.info(
            "Aguardando renderizacao completa do Starlink Fleet Usage e botao CSV (timeout=%ss).",
            int(timeout_ms / 1000),
        )

        while time.time() < deadline:
            if self._session_expired(page):
                return None

            csv_button = self._csv_locator(page)
            if csv_button is not None:
                elapsed = int(time.time() - started)
                self.logger.info(
                    "Starlink Fleet Usage pronto. Botao CSV visivel apos %ss.",
                    elapsed,
                )
                return csv_button

            if time.time() >= next_log:
                elapsed = int(time.time() - started)
                try:
                    ready_state = page.evaluate("document.readyState")
                except Exception:
                    ready_state = "unknown"
                try:
                    button_count = page.locator("button").count()
                except Exception:
                    button_count = -1
                try:
                    csv_text_count = page.get_by_text("CSV", exact=True).count()
                except Exception:
                    csv_text_count = -1
                try:
                    xlsx_text_count = page.get_by_text("XLSX", exact=True).count()
                except Exception:
                    xlsx_text_count = -1
                try:
                    body_len = len(page.locator("body").inner_text(timeout=1000))
                except Exception:
                    body_len = -1
                self.logger.info(
                    "Starlink Fleet Usage ainda carregando (%ss; readyState=%s; botoes=%s; CSV=%s; XLSX=%s; body=%s chars).",
                    elapsed, ready_state, button_count, csv_text_count, xlsx_text_count, body_len,
                )
                next_log = time.time() + 10

            time.sleep(poll_ms / 1000.0)

        return None

    def _download_csv(self, page, csv_button=None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Defensive wait: even if collect() already waited for readiness, do not
        # assume the SPA is stable if this method is reused independently.
        if csv_button is None:
            csv_button = self._wait_for_usage_page_ready(page)

        if csv_button is None:
            diag = self._diagnostic_snapshot(page, "csv_button_not_found")
            raise RuntimeError(
                "O botao CSV nao apareceu dentro do timeout da pagina Starlink Fleet Usage. "
                f"Diagnostico: {diag.get('meta')} | Screenshot: {diag.get('screenshot')}"
            )

        timeout_ms = int(self.config["compass"].get("download_timeout_ms", 90000))
        try:
            # Em SPAs o botao pode ser substituido durante uma re-renderizacao.
            # Revalide imediatamente antes do click.
            if not csv_button.is_visible():
                csv_button = self._csv_locator(page) or csv_button
            with page.expect_download(timeout=timeout_ms) as info:
                csv_button.click()
            download = info.value
            suggested = str(download.suggested_filename or "starlink_fleet_usage.csv")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", suggested).strip("_") or "starlink_fleet_usage.csv"
            destination = RAW_DIR / f"{stamp}_{safe}"
            download.save_as(str(destination))
            self.logger.info("CSV do Compass salvo em %s (nome portal: %s)", destination, suggested)
            return destination
        except Exception as exc:
            diag = self._diagnostic_snapshot(page, "csv_download_failed")
            raise RuntimeError(
                "O botao CSV apareceu, mas o download nao foi concluido. "
                f"Diagnostico: {diag.get('meta')} | Screenshot: {diag.get('screenshot')}. Erro: {exc}"
            ) from exc

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
        self.logger.info("Compass Collector build %s", AGENT_BUILD)
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
            context_args.update(self._video_context_args())
            if cfg.get("use_saved_session", True) and state_path.exists() and not reset_session:
                context_args["storage_state"] = str(state_path)
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.set_default_timeout(cfg.get("timeout_ms", 60000))
            usage_url = cfg.get("usage_url", "https://compass.speedcast.com/fleet/starlinkusage")
            has_saved_session = bool(
                cfg.get("use_saved_session", True) and state_path.exists() and not reset_session
            )

            if has_saved_session:
                # Com uma sessao persistida, podemos tentar o relatorio diretamente.
                # Se ela tiver expirado, o Compass redireciona para login e o fluxo
                # abaixo autentica e aguarda a estabilizacao antes de tentar novamente.
                self.logger.info("Verificando sessao salva antes de abrir Starlink Fleet Usage.")
                page.goto(usage_url, wait_until="domcontentloaded")
                if self._session_expired(page):
                    if not cfg.get("auto_login", True):
                        shot = self._failure_screenshot(page, "session_expired")
                        self._finalize_browser_run(context, page, browser, "session_expired")
                        raise RuntimeError(
                            "A sessao do Compass expirou e auto_login esta desabilitado. "
                            f"Screenshot salvo em {shot}."
                        )
                    try:
                        self._login(page)
                        self.logger.info("Abrindo Starlink Fleet Usage apos estabilizacao: %s", usage_url)
                        page.goto(usage_url, wait_until="domcontentloaded")
                    except Exception as exc:
                        shot = self._failure_screenshot(page, "automatic_login_failed")
                        self._finalize_browser_run(context, page, browser, "login_failed")
                        raise RuntimeError(f"Falha no login automatico do Compass. Screenshot: {shot}. Erro: {exc}")
            else:
                # Primeira execucao/teste de login: NAO toque em /fleet/starlinkusage
                # antes de autenticar e terminar a janela de estabilizacao do portal.
                if not cfg.get("auto_login", True):
                    self._finalize_browser_run(context, page, browser, "no_session")
                    raise RuntimeError(
                        "Nao existe sessao salva e auto_login esta desabilitado. "
                        "Habilite auto_login ou forneca um storage_state valido."
                    )
                try:
                    self.logger.info("Sem sessao salva. Iniciando pelo login do Compass antes de acessar o relatorio.")
                    self._login(page)
                    self.logger.info("Abrindo Starlink Fleet Usage somente apos estabilizacao: %s", usage_url)
                    page.goto(usage_url, wait_until="domcontentloaded")
                except Exception as exc:
                    shot = self._failure_screenshot(page, "automatic_login_failed")
                    self._finalize_browser_run(context, page, browser, "login_failed")
                    raise RuntimeError(f"Falha no login automatico do Compass. Screenshot: {shot}. Erro: {exc}")

            # Aguarde o relatorio real ficar pronto. Nao use o texto do menu como
            # sinal de readiness: ele pode aparecer antes do conteudo e do botao CSV.
            csv_button = self._wait_for_usage_page_ready(page)
            if csv_button is None and self._session_expired(page):
                # Redirecionamento tardio para login: autentique e reabra uma vez.
                try:
                    self._login(page)
                    self.logger.info("Reabrindo Starlink Fleet Usage apos estabilizacao: %s", usage_url)
                    page.goto(usage_url, wait_until="domcontentloaded")
                    csv_button = self._wait_for_usage_page_ready(page)
                except Exception as exc:
                    diag = self._diagnostic_snapshot(page, "late_login_failed")
                    self._finalize_browser_run(context, page, browser, "late_login_failed")
                    raise RuntimeError(
                        f"Falha de autenticacao durante abertura do relatorio. Diagnostico: {diag}. Erro: {exc}"
                    )

            if csv_button is None:
                diag = self._diagnostic_snapshot(page, "usage_page_not_ready")
                self._finalize_browser_run(context, page, browser, "usage_not_ready")
                raise RuntimeError(
                    "Pagina Starlink Fleet Usage nao ficou pronta dentro do timeout. "
                    f"URL final: {diag.get('url')}. Titulo: {diag.get('title')}. "
                    f"Diagnostico: {diag.get('meta')} | Screenshot: {diag.get('screenshot')}"
                )

            try:
                period = self._period_label(page)
                csv_path = self._download_csv(page, csv_button=csv_button)
                # Mantem cookies/tokens para acelerar a proxima execucao. Se expirarem, o agente reloga sozinho.
                context.storage_state(path=str(state_path))
            except Exception:
                self._finalize_browser_run(context, page, browser, "download_failed")
                raise
            self._finalize_browser_run(context, page, browser, "success")

        rows = self._parse_csv(csv_path, period)
        self.logger.info("Coleta Compass concluida: %d unidade(s)", len(rows))
        return rows
