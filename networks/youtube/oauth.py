"""Flux OAuth Google (YouTube Data v3) avec PKCE — application « Desktop ».

Ouvre la fenêtre de consentement Google, capte le `code` sur un mini-serveur
local (loopback), l'échange contre access/refresh tokens. Gratuit : un projet
Google Cloud + l'API YouTube Data v3 (quota gratuit) suffisent, aucun audit.

Mêmes conventions que le module OAuth TikTok : les fonctions renvoient des dicts
de tokens ; le plugin les persiste dans les credentials chiffrés du compte.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config as cfg

logger = logging.getLogger(__name__)

_result: dict = {}


def _callback_port(redirect: str) -> int:
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
        _result["code"] = q.get("code", [None])[0]
        _result["state"] = q.get("state", [None])[0]
        _result["error"] = q.get("error", [None])[0]
        ok = _result.get("code") and not _result.get("error")
        msg = ("Compte YouTube autorisé. Vous pouvez fermer cet onglet et revenir "
               "à l'application." if ok else
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
    # Google : code_challenge = BASE64URL(SHA256(verifier)), sans padding.
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _build_authorize_url(config: dict, state: str, challenge: str) -> str:
    params = {
        "client_id": cfg.client_id(config),
        "redirect_uri": cfg.redirect_uri(config),
        "response_type": "code",
        "scope": cfg.SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",   # refresh_token sur le 1er consentement
        "prompt": "consent",        # force la délivrance d'un refresh_token
    }
    return f"{cfg.AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def authorize(config: dict, on_log=None, timeout_s: int = 300) -> dict:
    """Lance le flux OAuth Google. Retourne {'success', 'tokens'|None, 'error'|None}.

    tokens = {access_token, refresh_token, expires_at(iso)}.
    """
    def log(m):
        logger.info(m)
        if on_log:
            on_log(m)

    if cfg.simulate(config):
        log("[SIMULATION] Liaison YouTube factice (aucun appel Google).")
        return {"success": True, "error": None, "tokens": {
            "access_token": "SIMULATED_TOKEN",
            "refresh_token": "SIMULATED_REFRESH",
            "expires_at": (datetime.now() + timedelta(days=1)).isoformat(),
            "channel": f"sim_{secrets.token_hex(3)}",
        }}

    if not cfg.has_keys(config):
        return {"success": False, "tokens": None,
                "error": "Identifiants OAuth Google manquants (Client ID / Secret)."}

    redirect = cfg.redirect_uri(config)
    port = _callback_port(redirect)
    _result.clear()
    state = secrets.token_urlsafe(16)
    verifier, challenge = _make_pkce()

    try:
        server = HTTPServer(("localhost", port), _Handler)
    except OSError as e:
        return {"success": False, "tokens": None,
                "error": f"Port {port} indisponible : {e}"}
    server.timeout = 1

    log("Ouverture de la fenêtre de consentement Google…")
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
        return {"success": False, "tokens": None, "error": f"Refusé : {_result['error']}"}
    if _result.get("state") != state:
        return {"success": False, "tokens": None, "error": "state OAuth invalide (sécurité)."}
    code = _result.get("code")
    if not code:
        return {"success": False, "tokens": None, "error": "Aucun code reçu (délai dépassé)."}

    try:
        resp = requests.post(
            cfg.TOKEN_URL,
            data={
                "client_id": cfg.client_id(config),
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
        logger.info("Token exchange Google (HTTP %s)", resp.status_code)
    except Exception as e:
        return {"success": False, "tokens": None, "error": f"Échange du token échoué : {e}"}

    if data.get("error"):
        err = data.get("error_description") or data.get("error")
        logger.error("Token exchange Google error: %s", err)
        return {"success": False, "tokens": None, "error": err}

    access = data.get("access_token")
    if not access:
        return {"success": False, "tokens": None, "error": f"Réponse token inattendue : {data}"}

    expires_at = datetime.now() + timedelta(seconds=int(data.get("expires_in", 3600)))
    log("Compte YouTube lié (tokens stockés, chiffrés).")
    return {"success": True, "error": None, "tokens": {
        "access_token": access,
        "refresh_token": data.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
    }}


def refresh(config: dict, refresh_token: str) -> dict | None:
    """Rafraîchit l'access_token. Retourne de nouveaux tokens ou None."""
    if not refresh_token:
        return None
    try:
        resp = requests.post(
            cfg.TOKEN_URL,
            data={
                "client_id": cfg.client_id(config),
                "client_secret": cfg.client_secret(config),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        data = resp.json()
    except Exception as e:
        logger.error("Refresh token Google échoué : %s", e)
        return None
    if data.get("error") or not data.get("access_token"):
        logger.error("Refresh Google refusé : %s",
                     data.get("error_description") or data.get("error"))
        return None
    expires_at = datetime.now() + timedelta(seconds=int(data.get("expires_in", 3600)))
    return {
        "access_token": data["access_token"],
        # Google ne renvoie pas toujours un nouveau refresh_token : on garde l'ancien.
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
            # Marge de 60 s pour éviter d'utiliser un token qui expire pendant l'upload.
            expired = datetime.fromisoformat(expires_at) <= datetime.now() + timedelta(seconds=60)
        except (ValueError, TypeError):
            expired = False
    if expired and credentials.get("refresh_token"):
        new = refresh(config, credentials["refresh_token"])
        if new:
            if on_refresh:
                on_refresh(new)
            return new["access_token"]
    return token


def refresh_access_token(config: dict, credentials: dict, on_refresh=None) -> str | None:
    """Force un refresh immédiat (e.g. après un 401 inattendu pendant l'upload)."""
    rt = credentials.get("refresh_token")
    if not rt:
        return None
    new = refresh(config, rt)
    if not new:
        return None
    if on_refresh:
        on_refresh(new)
    return new["access_token"]
