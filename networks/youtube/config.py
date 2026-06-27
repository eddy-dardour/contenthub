"""Configuration YouTube : résolution des options OAuth + publication.

Contrairement à TikTok, YouTube n'offre PAS de clés sandbox partageables : chaque
utilisateur doit créer un projet Google Cloud (gratuit), activer l'API YouTube
Data v3 et coller son Client ID / Secret OAuth « Desktop ». Tant que ces clés ne
sont pas saisies, le réseau reste en Standby (cf. plugin).

Un mode SIMULATION (publication factice, aucun appel Google) permet de tester
toute la chaîne hors-ligne — identique à TikTok.
"""

from __future__ import annotations

import os

# Redirection en boucle locale (loopback) — recommandée par Google pour les
# applications « Desktop ». Le port doit être libre ; on en choisit un dédié pour
# ne pas entrer en collision avec le callback TikTok (8723).
DEFAULT_REDIRECT_URI = "http://localhost:8724/callback"

# Shorts : la vidéo verticale (1080x1920, < 60 s) est détectée automatiquement
# par YouTube comme un Short. Pour la MONÉTISATION (YPP Shorts), la vidéo doit
# être PUBLIQUE → confidentialité par défaut « public ».
DEFAULT_PRIVACY_STATUS = "public"   # public | unlisted | private

# « Made for kids » (COPPA) : par défaut False. Pourra être basculé à True quand
# le contenu ciblera explicitement les enfants (cf. roadmap contenu).
DEFAULT_MADE_FOR_KIDS = False

# Scope minimal pour uploader une vidéo sur la chaîne de l'utilisateur.
SCOPES = "https://www.googleapis.com/auth/youtube.upload"

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


def client_id(config: dict) -> str:
    return (config.get("client_id") or os.getenv("YOUTUBE_CLIENT_ID") or "").strip()


def client_secret(config: dict) -> str:
    return (config.get("client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()


def redirect_uri(config: dict) -> str:
    return config.get("redirect_uri") or DEFAULT_REDIRECT_URI


def privacy_status(config: dict) -> str:
    status = (config.get("privacy_status") or "").strip().lower()
    return status if status in ("public", "unlisted", "private") else DEFAULT_PRIVACY_STATUS


def made_for_kids(config: dict) -> bool:
    """Déclaration COPPA. Bascule à True quand le contenu cible les enfants."""
    val = config.get("made_for_kids")
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "oui", "yes")
    return bool(val) if val is not None else DEFAULT_MADE_FOR_KIDS


def has_keys(config: dict) -> bool:
    """True si les identifiants OAuth personnels sont présents."""
    return bool(client_id(config) and client_secret(config))


def simulate(config: dict) -> bool:
    """True = publication factice, aucun appel Google (démo hors-ligne)."""
    return bool(config.get("simulate"))
