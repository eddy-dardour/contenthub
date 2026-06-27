"""Plugin réseau Facebook — publie des vidéos/Reels sur une Page via Graph API.

État :
  • STANDBY tant que app_id ou app_secret ne sont pas renseignés.
  • CONFIGURED dès que les deux clés sont présentes (ou en mode simulation).

La liaison OAuth récupère un Page Access Token long (≈60 jours) stocké chiffré.
"""

from __future__ import annotations

import logging

from networks.base import NetworkPlugin
from core.models import Account, ContentItem, PublishResult, NetworkState

from . import config as cfg
from . import oauth
from .uploader import Uploader

logger = logging.getLogger(__name__)


class FacebookNetwork(NetworkPlugin):
    id           = "facebook"
    display_name = "Facebook"
    icon         = "facebook"
    description  = "Publication de vidéos / Reels sur une Page Facebook via Graph API."
    config_fields = {
        "app_id":       "App ID (Facebook Developers)",
        "app_secret":   "App Secret (Facebook Developers)",
        "redirect_uri": "Redirect URI (défaut : http://localhost:8724/callback)",
        "privacy":      "Confidentialité (EVERYONE / FRIENDS / SELF)",
        "simulate":     "Simulation (1 = aucun appel réseau)",
    }

    # ── État ────────────────────────────────────────────────────────────

    def _evaluate_state(self, config: dict) -> NetworkState:
        if cfg.simulate(config):
            return NetworkState.CONFIGURED
        if cfg.app_id(config) and cfg.app_secret(config):
            return NetworkState.CONFIGURED
        return NetworkState.STANDBY

    def status_note(self, state: NetworkState) -> str:
        config = self.load_config()
        if cfg.simulate(config):
            return "Mode simulation (démo hors-ligne, aucun appel Facebook)."
        if state == NetworkState.STANDBY:
            return "En attente de configuration : renseignez App ID et App Secret."
        return "Prêt à publier sur votre Page Facebook."

    # ── Liaison de compte ───────────────────────────────────────────────

    def link_account(self, account: Account, on_log=None) -> PublishResult:
        config = self.load_config()
        if self._evaluate_state(config) == NetworkState.STANDBY:
            return PublishResult(
                False,
                "Facebook est en Standby : renseignez App ID et App Secret "
                "dans la configuration du réseau.")

        result = oauth.authorize(config, on_log=on_log)
        if not result["success"]:
            return PublishResult(False, result["error"] or "Liaison échouée.")

        tokens = result["tokens"]
        page_name = tokens.get("page_name", account.name)
        self.accounts.set_credentials(
            account.id, tokens, handle=tokens.get("page_id"))
        return PublishResult(
            True,
            f"Page « {page_name} » liée.",
            remote_id=tokens.get("page_id"),
        )

    def is_account_linked(self, account: Account) -> bool:
        return bool(account.credentials.get("page_access_token"))

    # ── Publication ─────────────────────────────────────────────────────

    def publish(self, account: Account, content: ContentItem,
                on_log=None) -> PublishResult:
        config = self.load_config()

        if not oauth.valid_token(account.credentials):
            return PublishResult(
                False,
                f"Compte « {account.name} » non lié ou token manquant. "
                "Reliez le compte depuis l'onglet Comptes.")

        up = Uploader(config, account_name=account.name)
        ok, detail = up.upload(
            account.credentials, content.path, content.caption, on_log=on_log)
        if ok:
            return PublishResult(True, "Publié sur Facebook.")
        return PublishResult(False, detail or "Échec de l'upload Facebook.")

    # ── Stats distantes (Graph API) ─────────────────────────────────────

    def fetch_stats(self, account: Account) -> dict | None:
        import requests
        if cfg.simulate(self.load_config()):
            return None
        token = account.credentials.get("page_access_token")
        page_id = account.credentials.get("page_id")
        if not token or not page_id:
            return None
        try:
            # Nombre total de vidéos publiées sur la Page.
            v = requests.get(
                f"{cfg.GRAPH_BASE}/{page_id}/videos",
                params={"access_token": token, "summary": "true", "limit": "1"},
                timeout=15).json()
            videos = (v.get("summary") or {}).get("total_count")
            # Followers de la Page (proxy de portée).
            p = requests.get(
                f"{cfg.GRAPH_BASE}/{page_id}",
                params={"access_token": token, "fields": "followers_count"},
                timeout=15).json()
            return {
                "videos": videos,
                "views": p.get("followers_count"),  # proxy : abonnés Page
                "likes": None,
            }
        except Exception as e:
            logger.warning("Facebook fetch_stats échoué : %s", e)
            return None


PLUGIN = FacebookNetwork
