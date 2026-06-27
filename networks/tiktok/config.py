"""Configuration TikTok : clés sandbox par défaut + résolution des options.

Si l'utilisateur n'a pas saisi ses propres clés (réseau en Standby), on retombe
sur les identifiants SANDBOX fournis → l'app est démontrable immédiatement
(comptes testeurs, publication privée), sans audit TikTok.

Un mode SIMULATION (publication factice) permet de tester toute la plateforme
sans aucun appel réseau — pratique pour enregistrer la vidéo de démonstration.
"""

from __future__ import annotations

# ── Clés SANDBOX (démo/audit TikTok). Remplaçables par les clés de l'utilisateur
#    via la configuration du réseau dans l'UI. ────────────────────────────────
SANDBOX_CLIENT_KEY = "sbawdcq6su56fu6l39"
SANDBOX_CLIENT_SECRET = "su3YRkJ4ax9neSg5cnR5TijU0zdBXDEC"

DEFAULT_REDIRECT_URI = "http://localhost:8723/callback"
DEFAULT_PRIVACY_LEVEL = "SELF_ONLY"

SCOPES = "user.info.basic,video.upload,video.publish"


def client_key(config: dict) -> str:
    return config.get("client_key") or SANDBOX_CLIENT_KEY


def client_secret(config: dict) -> str:
    return config.get("client_secret") or SANDBOX_CLIENT_SECRET


def redirect_uri(config: dict) -> str:
    return config.get("redirect_uri") or DEFAULT_REDIRECT_URI


def privacy_level(config: dict) -> str:
    return config.get("privacy_level") or DEFAULT_PRIVACY_LEVEL


def is_sandbox(config: dict) -> bool:
    """True si aucune clé personnelle n'est saisie (on utilise la sandbox)."""
    return not config.get("client_key")


def simulate(config: dict) -> bool:
    """True = publication factice, aucun appel TikTok (démo hors-ligne)."""
    return bool(config.get("simulate"))
