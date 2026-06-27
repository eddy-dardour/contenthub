"""Flux OAuth Facebook (Login v19.0) — code flow avec mini-serveur local.

Ouvre la fenêtre d'autorisation Facebook, reçoit le code sur localhost:8724/callback,
l'échange contre un User Token court, puis récupère un Page Token long (60 j).

Tokens renvoyés :
  user_access_token   : token utilisateur (court, pour récupérer les Pages)
  page_id             : identifiant de la Page sélectionnée
  page_name           : nom de la Page
  page_access_token   : token de Page (long, pour publier)
  expires_at          : ISO datetime d'expiration approximative (60 j)
"""

from __future__ import annotations

import time
import logging
import secrets
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config as cfg

logger = logging.getLogger(__name__)

_result: dict = {}


def _port(redirect: str) -> int:
    try:
        return int(urllib.parse.urlparse(redirect).port or 8724)
    except Exception:
        return 8724


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        _result["code"]  = q.get("code",  [None])[0]
        _result["state"] = q.get("state", [None])[0]
        _result["error"] = q.get("error", [None])[0]
        ok = _result.get("code") and not _result.get("error")
        msg = ("Compte autorisé. Fermez cet onglet et revenez à l'application."
               if ok else
               "Autorisation refusée. Fermez cet onglet et réessayez.")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            f'<html><body style="font-family:system-ui;background:#0f1115;'
            f'color:#e6e6e6;text-align:center;padding-top:80px">'
            f'<h2>{msg}</h2></body></html>'.encode("utf-8"))

    def log_message(self, *_):
        pass


def _build_auth_url(config: dict, state: str) -> str:
    params = urllib.parse.urlencode({
        "client_id":     cfg.app_id(config),
        "redirect_uri":  cfg.redirect_uri(config),
        "scope":         cfg.SCOPES,
        "response_type": "code",
        "state":         state,
    })
    return f"{cfg.DIALOG_URL}?{params}"


def _exchange_code(config: dict, code: str) -> dict | None:
    """Échange le code contre un User Access Token court."""
    try:
        resp = requests.get(cfg.TOKEN_URL, params={
            "client_id":     cfg.app_id(config),
            "client_secret": cfg.app_secret(config),
            "redirect_uri":  cfg.redirect_uri(config),
            "code":          code,
        }, timeout=15)
        data = resp.json()
        logger.debug("Token exchange: %s", data)
        return data if "access_token" in data else None
    except Exception as e:
        logger.error("Échange token échoué : %s", e)
        return None


def _long_lived_user_token(config: dict, short_token: str) -> str | None:
    """Prolonge le User Token à ~60 jours."""
    try:
        resp = requests.get(cfg.TOKEN_URL, params={
            "grant_type":        "fb_exchange_token",
            "client_id":         cfg.app_id(config),
            "client_secret":     cfg.app_secret(config),
            "fb_exchange_token": short_token,
        }, timeout=15)
        data = resp.json()
        return data.get("access_token")
    except Exception as e:
        logger.error("Prolongation token échouée : %s", e)
        return None


def _fetch_pages(user_token: str) -> list[dict]:
    """Retourne la liste {id, name, access_token} des Pages gérées."""
    try:
        resp = requests.get(
            f"{cfg.GRAPH_BASE}/me/accounts",
            params={"access_token": user_token, "fields": "id,name,access_token"},
            timeout=15,
        )
        return resp.json().get("data", [])
    except Exception as e:
        logger.error("Récupération Pages échouée : %s", e)
        return []


def authorize(config: dict, on_log=None, timeout_s: int = 300) -> dict:
    """Lance le flux OAuth Facebook.

    Retourne {'success', 'tokens'|None, 'error'|None, 'pages'|[]}.
    Si plusieurs Pages existent, tokens contient la première ; l'appelant peut
    laisser l'UI faire choisir (cf. plugin.link_account).
    """
    def log(m):
        logger.info(m)
        if on_log:
            on_log(m)

    if cfg.simulate(config):
        log("[SIMULATION] Liaison Facebook factice.")
        page_id = f"sim_{secrets.token_hex(3)}"
        return {"success": True, "error": None, "pages": [
            {"id": page_id, "name": "Page Simulée",
             "access_token": "SIMULATED_PAGE_TOKEN"}
        ], "tokens": {
            "page_id":           page_id,
            "page_name":         "Page Simulée",
            "page_access_token": "SIMULATED_PAGE_TOKEN",
            "expires_at":        (datetime.now() + timedelta(days=60)).isoformat(),
        }}

    redirect = cfg.redirect_uri(config)
    port = _port(redirect)
    _result.clear()
    state = secrets.token_urlsafe(16)

    try:
        server = HTTPServer(("localhost", port), _Handler)
    except OSError as e:
        return {"success": False, "error": f"Port {port} indisponible : {e}",
                "pages": [], "tokens": None}
    server.timeout = 1

    log("Ouverture de la fenêtre d'autorisation Facebook…")
    webbrowser.open(_build_auth_url(config, state))

    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline and "code" not in _result and "error" not in _result:
            server.handle_request()
    finally:
        try:
            server.server_close()
        except Exception:
            pass

    if _result.get("error"):
        return {"success": False, "error": f"Refusé : {_result['error']}",
                "pages": [], "tokens": None}
    if _result.get("state") != state:
        return {"success": False, "error": "state OAuth invalide (sécurité).",
                "pages": [], "tokens": None}
    code = _result.get("code")
    if not code:
        return {"success": False, "error": "Aucun code reçu (délai dépassé).",
                "pages": [], "tokens": None}

    token_data = _exchange_code(config, code)
    if not token_data:
        return {"success": False, "error": "Échange du code échoué.",
                "pages": [], "tokens": None}

    short_token = token_data["access_token"]
    log("Code échangé. Prolongation du token utilisateur…")
    long_token = _long_lived_user_token(config, short_token) or short_token

    log("Récupération des Pages Facebook gérées…")
    pages = _fetch_pages(long_token)
    if not pages:
        return {"success": False,
                "error": "Aucune Page Facebook trouvée pour ce compte. "
                         "Assurez-vous d'être administrateur d'une Page.",
                "pages": [], "tokens": None}

    # On retourne toutes les Pages ; le plugin choisit la première par défaut.
    first = pages[0]
    expires_at = (datetime.now() + timedelta(days=60)).isoformat()
    log(f"Page trouvée : « {first['name']} » ({first['id']}). Liaison réussie.")
    return {
        "success": True,
        "error": None,
        "pages": pages,
        "tokens": {
            "page_id":           first["id"],
            "page_name":         first["name"],
            "page_access_token": first["access_token"],
            "expires_at":        expires_at,
        },
    }


def valid_token(credentials: dict) -> str | None:
    """Retourne le page_access_token si présent (Pages tokens n'ont pas de refresh)."""
    return credentials.get("page_access_token") or None
