"""Configuration TikTok : résolution des options de publication.

Les clés officielles (Client Key / Secret) sont saisies par l'utilisateur dans
la configuration du réseau. Tant qu'elles ne sont pas renseignées, le réseau
reste en mode STANDBY (affichage seul, aucune publication).

Un mode SIMULATION (publication factice) permet de tester toute la plateforme
sans aucun appel réseau.
"""

from __future__ import annotations

import os

# Pas de clés hardcodées — TikTok requiert des clés personnelles (pas de sandbox partagée).
# L'utilisateur renseigne client_key / client_secret dans l'onglet Réseaux → Configuration.
SANDBOX_CLIENT_KEY    = os.getenv("TIKTOK_SANDBOX_KEY", "")
SANDBOX_CLIENT_SECRET = os.getenv("TIKTOK_SANDBOX_SECRET", "")

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
