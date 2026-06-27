"""Configuration Facebook : résolution des options et constantes API.

L'utilisateur fournit un App ID + App Secret via la config du réseau. Sans ces
clés le plugin reste en STANDBY. Un mode SIMULATION permet de tester sans appel
réseau.
"""

from __future__ import annotations

# ── Endpoints Graph API ──────────────────────────────────────────────────────
GRAPH_BASE = "https://graph.facebook.com/v19.0"
DIALOG_URL = "https://www.facebook.com/v19.0/dialog/oauth"
TOKEN_URL   = f"{GRAPH_BASE}/oauth/access_token"

# Scopes nécessaires pour uploader des vidéos/Reels sur une Page Facebook.
SCOPES = "pages_show_list,pages_read_engagement,pages_manage_posts,publish_video"

DEFAULT_REDIRECT_URI = "http://localhost:8724/callback"
DEFAULT_PRIVACY = "EVERYONE"   # valeur Pages API : EVERYONE | FRIENDS | SELF


def app_id(config: dict) -> str:
    return config.get("app_id", "")


def app_secret(config: dict) -> str:
    return config.get("app_secret", "")


def redirect_uri(config: dict) -> str:
    return config.get("redirect_uri") or DEFAULT_REDIRECT_URI


def privacy(config: dict) -> str:
    return config.get("privacy") or DEFAULT_PRIVACY


def simulate(config: dict) -> bool:
    return bool(config.get("simulate"))
