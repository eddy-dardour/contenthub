"""Flux OAuth officiel TikTok (Login Kit) avec PKCE.

Ouvre la fenêtre d'autorisation TikTok, capte le `code` sur un mini-serveur
local, l'échange contre access/refresh tokens. Aucun audit requis pour CE flux :
OAuth + upload privé/brouillon fonctionnent en sandbox avec des comptes testeurs.

Les fonctions renvoient des dicts de tokens ; c'est l'appelant (le plugin) qui
les persiste dans les credentials chiffrés du compte.
"""

from __future__ import annotations

import time
import hashlib
import logging
import secrets
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config as cfg

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"

_result: dict = {}


def _callback_port(redirect: str) -> int:
    try:
        return int(urllib.parse.urlparse(redirect).port or 8723)
    except Exception:
        return 8723


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/callback"):
            self.send_response(404)
            self.end_headers()
            return
        q = urllib.parse.parse_qs(parsed.query)
        _result["code"] = q.get("code", [None])[0]
        _result["state"] = q.get("state", [None])[0]
        _result["error"] = q.get("error", [None])[0]
        ok = _result.get("code") and not _result.get("error")
        msg = ("Compte autorisé. Vous pouvez fermer cet onglet et revenir à "
               "l'application." if ok else
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


def _make_pkce() -> tuple[str, str]:
    # TikTok Desktop Login Kit : code_challenge = SHA256(verifier) en HEX (pas base64).
    # Verifier : [A-Za-z0-9\-._~], 43-128 chars.
    verifier = secrets.token_urlsafe(32)  # 43 chars urlsafe
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def _build_authorize_url(config: dict, state: str, challenge: str) -> str:
    # scope doit avoir une virgule brute (pas %2C) → URL construite manuellement.
    redirect = urllib.parse.quote(cfg.redirect_uri(config), safe="")
    return (
        f"{AUTHORIZE_URL}?client_key={cfg.client_key(config)}"
        f"&scope={cfg.SCOPES}"
        f"&response_type=code"
        f"&redirect_uri={redirect}"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )


def authorize(config: dict, on_log=None, timeout_s: int = 300) -> dict:
    """Lance le flux OAuth. Retourne {'success', 'tokens'|None, 'error'|None}.

    tokens = {access_token, refresh_token, expires_at(iso), open_id}.
    """
    def log(m):
        logger.info(m)
        if on_log:
            on_log(m)

    if cfg.simulate(config):
        log("[SIMULATION] Liaison factice (aucun appel TikTok).")
        return {"success": True, "error": None, "tokens": {
            "access_token": "SIMULATED_TOKEN",
            "refresh_token": "SIMULATED_REFRESH",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "open_id": f"sim_{secrets.token_hex(3)}",
        }}

    redirect = cfg.redirect_uri(config)
    port = _callback_port(redirect)
    _result.clear()
    state = secrets.token_urlsafe(16)
    verifier, challenge = _make_pkce()

    try:
        server = HTTPServer(("localhost", port), _Handler)
    except OSError as e:
        return {"success": False, "error": f"Port {port} indisponible : {e}", "tokens": None}
    server.timeout = 1

    log("Ouverture de la fenêtre d'autorisation TikTok…")
    webbrowser.open(_build_authorize_url(config, state, challenge))

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
        return {"success": False, "error": f"Refusé : {_result['error']}", "tokens": None}
    if _result.get("state") != state:
        return {"success": False, "error": "state OAuth invalide (sécurité).", "tokens": None}
    code = _result.get("code")
    if not code:
        return {"success": False, "error": "Aucun code reçu (délai dépassé).", "tokens": None}

    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_key": cfg.client_key(config),
                "client_secret": cfg.client_secret(config),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
        logger.info("Token exchange response (HTTP %s): %s", resp.status_code, data)
    except Exception as e:
        return {"success": False, "error": f"Échange du token échoué : {e}", "tokens": None}

    if data.get("error"):
        err_detail = data.get("error_description") or data.get("error")
        logger.error("Token exchange error: %s | full response: %s", err_detail, data)
        return {"success": False, "error": err_detail, "tokens": None}

    access = data.get("access_token")
    if not access:
        return {"success": False, "error": f"Réponse token inattendue : {data}", "tokens": None}

    expires_at = datetime.now() + timedelta(seconds=int(data.get("expires_in", 86400)))
    log("Compte TikTok lié (tokens stockés, chiffrés).")
    return {"success": True, "error": None, "tokens": {
        "access_token": access,
        "refresh_token": data.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
        "open_id": data.get("open_id"),
    }}


def refresh(config: dict, refresh_token: str) -> dict | None:
    """Rafraîchit l'access_token. Retourne de nouveaux tokens ou None."""
    if not refresh_token:
        return None
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_key": cfg.client_key(config),
                "client_secret": cfg.client_secret(config),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        logger.error("Refresh token échoué : %s", e)
        return None
    if data.get("error") or not data.get("access_token"):
        logger.error("Refresh refusé : %s", data.get("error_description") or data.get("error"))
        return None
    expires_at = datetime.now() + timedelta(seconds=int(data.get("expires_in", 86400)))
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_at": expires_at.isoformat(),
    }


def valid_token(config: dict, credentials: dict, on_refresh=None) -> str | None:
    """Retourne un access_token valide, rafraîchi si expiré.

    `on_refresh(new_tokens)` est appelé si un refresh a eu lieu, pour persister.
    """
    token = credentials.get("access_token")
    if not token:
        return None
    if cfg.simulate(config):
        return token
    expires_at = credentials.get("expires_at")
    expired = False
    if expires_at:
        try:
            expired = datetime.fromisoformat(expires_at) <= datetime.now()
        except (ValueError, TypeError):
            expired = False
    if expired and credentials.get("refresh_token"):
        new = refresh(config, credentials["refresh_token"])
        if new:
            if on_refresh:
                on_refresh(new)
            return new["access_token"]
    return token
