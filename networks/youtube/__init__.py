"""Plugin réseau YouTube — publication de Shorts via l'API Data v3.

État :
  • STANDBY tant que les identifiants OAuth Google (Client ID/Secret) ne sont pas
    saisis : YouTube n'offre pas de clés sandbox partageables.
  • CONFIGURED dès que les clés sont présentes → liaison OAuth + upload réels.

La vidéo verticale (1080x1920, < 60 s) générée pour TikTok est réutilisée telle
quelle : YouTube la classe automatiquement comme Short (format 3 du bundle).
"""

from __future__ import annotations

import logging

from networks.base import NetworkPlugin
from core.models import Account, ContentItem, PublishResult, NetworkState

from . import config as cfg
from . import oauth
from .uploader import Uploader

logger = logging.getLogger(__name__)


class YouTubeNetwork(NetworkPlugin):
    id = "youtube"
    display_name = "YouTube"
    icon = "youtube"
    description = "YouTube Shorts via l'API officielle Data v3 (OAuth Google gratuit)."
    config_fields = {
        "client_id": "OAuth Client ID (Google Cloud)",
        "client_secret": "OAuth Client Secret",
        "privacy_status": "Confidentialité (public pour monétiser / unlisted / private)",
        "made_for_kids": "Contenu pour enfants (1 = oui, COPPA)",
        "simulate": "Simulation (1 = aucun appel réseau)",
    }

    def _evaluate_state(self, config: dict) -> NetworkState:
        if cfg.simulate(config) or cfg.has_keys(config):
            return NetworkState.CONFIGURED
        return NetworkState.STANDBY

    def status_note(self, state: NetworkState) -> str:
        config = self.load_config()
        if cfg.simulate(config):
            return "Mode simulation (démo hors-ligne, aucun appel Google)."
        if state == NetworkState.STANDBY:
            return ("Standby — créez un projet Google Cloud (API YouTube Data v3, "
                    "gratuit), type « Desktop », et collez Client ID / Secret OAuth.")
        src = "env" if not config.get("client_id") else "config"
        return f"Identifiants présents ({src}) — prêt à publier des Shorts ({cfg.privacy_status(config)})."

    # ── Liaison de compte ───────────────────────────────────────────────

    def link_account(self, account: Account, on_log=None) -> PublishResult:
        config = self.load_config()
        if self._evaluate_state(config) == NetworkState.STANDBY:
            return PublishResult(
                False,
                "YouTube est en Standby : renseignez vos identifiants OAuth Google "
                "dans la configuration du réseau pour activer la liaison.")
        result = oauth.authorize(config, on_log=on_log)
        if not result["success"]:
            return PublishResult(False, result["error"] or "Liaison échouée.")
        tokens = result["tokens"]
        self.accounts.set_credentials(account.id, tokens, handle=tokens.get("channel"))
        return PublishResult(True, "Compte YouTube lié.", remote_id=tokens.get("channel"))

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
        ok, detail, video_id = up.upload(token, content.path, content.caption, on_log=on_log)

        # Retry automatique si 401 (token révoqué entre valid_token et l'upload réel).
        if not ok and detail and "401" in detail:
            logger.warning("[%s] 401 pendant upload — tentative de refresh.", account.name)
            fresh_token = oauth.refresh_access_token(config, account.credentials,
                                                     on_refresh=persist_refresh)
            if fresh_token:
                if on_log:
                    on_log("  ↻ Token rafraîchi, nouvel essai…")
                ok, detail, video_id = up.upload(fresh_token, content.path, content.caption, on_log=on_log)

        if ok:
            return PublishResult(True, "Short publié.", remote_id=video_id)
        return PublishResult(False, detail or "Échec de l'upload YouTube.")

    # ── Stats distantes (API Data v3) ───────────────────────────────────

    def fetch_stats(self, account: Account) -> dict | None:
        """Vues + likes agrégés sur les vidéos publiées par ContentHub.

        On lit les IDs YouTube des vidéos postées (table jobs, remote_id) et on
        interroge videos.list?part=statistics → somme des viewCount/likeCount.
        Si aucun id n'est encore enregistré (anciens posts), on retombe sur les
        stats de chaîne (vues totales) faute de mieux.
        """
        import requests
        config = self.load_config()
        if cfg.simulate(config):
            return None
        token = oauth.valid_token(
            config, account.credentials,
            on_refresh=lambda t: self.accounts.update_credentials(account.id, t))
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}

        # IDs des vidéos publiées par ce compte (avec remote_id non nul).
        from core.db import get_db
        rows = get_db().query(
            "SELECT remote_id FROM jobs WHERE account_id = ? AND status = 'success' "
            "AND remote_id IS NOT NULL AND remote_id != ''", (account.id,))
        video_ids = [r["remote_id"] for r in rows]

        try:
            if video_ids:
                total_views = total_likes = 0
                videos_counted = 0
                # videos.list accepte jusqu'à 50 ids par requête.
                for i in range(0, len(video_ids), 50):
                    batch = video_ids[i:i + 50]
                    resp = requests.get(
                        "https://www.googleapis.com/youtube/v3/videos",
                        params={"part": "statistics", "id": ",".join(batch)},
                        headers=headers, timeout=15)
                    for item in resp.json().get("items") or []:
                        s = item.get("statistics", {})
                        total_views += int(s.get("viewCount", 0) or 0)
                        total_likes += int(s.get("likeCount", 0) or 0)
                        videos_counted += 1
                return {
                    "videos": videos_counted,
                    "views": total_views,
                    "likes": total_likes,
                }

            # Fallback : stats de chaîne (pas de likes par vidéo disponibles).
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "statistics", "mine": "true"},
                headers=headers, timeout=15)
            items = resp.json().get("items") or []
            if not items:
                return None
            s = items[0].get("statistics", {})
            return {
                "videos": int(s.get("videoCount", 0)),
                "views": int(s.get("viewCount", 0)),
                "likes": None,  # non exposé au niveau chaîne
            }
        except Exception as e:
            logger.warning("YouTube fetch_stats échoué : %s", e)
            return None


PLUGIN = YouTubeNetwork
