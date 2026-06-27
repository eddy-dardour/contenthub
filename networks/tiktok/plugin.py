"""Plugin réseau TikTok — connecte la plateforme à l'API officielle TikTok.

État :
  • CONFIGURED dès le départ : des clés SANDBOX sont fournies, donc la
    publication (privée/brouillon, comptes testeurs) marche sans audit.
  • L'utilisateur peut saisir ses propres clés (client_key/secret) ou activer
    le mode SIMULATION via la configuration du réseau.
"""

from __future__ import annotations

import logging

from networks.base import NetworkPlugin
from core.models import Account, ContentItem, PublishResult, NetworkState

from . import config as cfg
from . import oauth
from .uploader import Uploader

logger = logging.getLogger(__name__)


class TikTokNetwork(NetworkPlugin):
    id = "tiktok"
    display_name = "TikTok"
    icon = "tiktok"
    description = "Publication via l'API officielle TikTok (gratuite, sans shadowban)."
    config_fields = {
        "client_key": "Client Key (laisser vide = sandbox)",
        "client_secret": "Client Secret (laisser vide = sandbox)",
        "privacy_level": "Confidentialité (SELF_ONLY / PUBLIC_TO_EVERYONE)",
        "simulate": "Simulation (1 = aucun appel réseau)",
    }

    def _evaluate_state(self, config: dict) -> NetworkState:
        # Toujours utilisable grâce aux clés sandbox ; jamais bloqué en Standby.
        return NetworkState.CONFIGURED

    def status_note(self, state: NetworkState) -> str:
        config = self.load_config()
        if cfg.simulate(config):
            return "Mode simulation (démo hors-ligne, aucun appel TikTok)."
        if cfg.is_sandbox(config):
            return "Clés sandbox : publication privée/brouillon, sans audit."
        return "Clés personnelles configurées. Prêt à publier."

    # ── Liaison de compte ───────────────────────────────────────────────

    def link_account(self, account: Account, on_log=None) -> PublishResult:
        config = self.load_config()
        result = oauth.authorize(config, on_log=on_log)
        if not result["success"]:
            return PublishResult(False, result["error"] or "Liaison échouée.")
        tokens = result["tokens"]
        self.accounts.set_credentials(
            account.id, tokens, handle=tokens.get("open_id"))
        return PublishResult(True, "Compte lié.", remote_id=tokens.get("open_id"))

    def is_account_linked(self, account: Account) -> bool:
        return bool(account.credentials.get("access_token"))

    # ── Publication ─────────────────────────────────────────────────────

    def publish(self, account: Account, content: ContentItem,
                on_log=None) -> PublishResult:
        config = self.load_config()

        def persist_refresh(new_tokens: dict):
            self.accounts.update_credentials(account.id, new_tokens)

        token = oauth.valid_token(config, account.credentials, on_refresh=persist_refresh)
        if not token:
            return PublishResult(
                False, f"Compte « {account.name} » non lié ou token expiré.")

        up = Uploader(config, account_name=account.name)
        ok, detail = up.upload(token, content.path, content.caption, on_log=on_log)
        if ok:
            return PublishResult(True, "Publié.")
        return PublishResult(False, detail or "Échec de l'upload TikTok.")

    # ── Stats distantes (best-effort) ───────────────────────────────────

    def fetch_stats(self, account: Account) -> dict | None:
        """Stats via user/info/. Nécessite le scope user.info.stats (non inclus
        par défaut) : retourne None proprement si non autorisé/sandbox."""
        import requests
        config = self.load_config()
        if cfg.simulate(config):
            return None
        token = oauth.valid_token(
            config, account.credentials,
            on_refresh=lambda t: self.accounts.update_credentials(account.id, t))
        if not token:
            return None
        try:
            resp = requests.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": "video_count,likes_count,follower_count"},
                headers={"Authorization": f"Bearer {token}"}, timeout=15)
            data = (resp.json().get("data") or {}).get("user") or {}
            if not data:
                return None
            return {
                "videos": data.get("video_count"),
                "views": data.get("follower_count"),  # proxy : abonnés
                "likes": data.get("likes_count"),
            }
        except Exception as e:
            logger.warning("TikTok fetch_stats échoué : %s", e)
            return None


PLUGIN = TikTokNetwork
