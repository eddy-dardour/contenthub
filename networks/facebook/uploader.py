"""Upload vidéo / Reel sur une Page Facebook via Graph API v19.0.

Utilise le flux « resumable upload » en deux étapes :
  1. POST /{page-id}/videos  → retourne un upload_url + video_id
  2. PUT <upload_url>        → envoi binaire par tranches (Content-Range)
  3. POST /{page-id}/videos  → publication avec video_id (si non publié en step 1)

En mode SIMULATION la vidéo est copiée localement, aucun appel réseau.
"""

from __future__ import annotations

import shutil
import logging
from pathlib import Path

import requests

from core.paths import app_data_dir
from . import config as cfg

logger = logging.getLogger(__name__)

CHUNK = 10 * 1024 * 1024   # 10 Mo par tranche
UPLOAD_INIT = "{base}/{page_id}/videos"


class Uploader:
    def __init__(self, config: dict, account_name: str = ""):
        self.config = config
        self.account_name = account_name or "compte"

    # ── Helpers HTTP ────────────────────────────────────────────────────

    @staticmethod
    def _graph_error(data: dict) -> str | None:
        err = (data or {}).get("error")
        if not err:
            return None
        return err.get("message") or err.get("code") or str(err)

    @staticmethod
    def _http_error(resp) -> str:
        try:
            data = resp.json()
            msg = (data.get("error") or {}).get("message") or resp.text[:200]
        except Exception:
            msg = resp.text[:200]
        return f"HTTP {resp.status_code} : {msg}"

    # ── Upload resumable ────────────────────────────────────────────────

    def _init_upload(self, page_token: str, page_id: str,
                     video: Path, caption: str) -> tuple[str | None, str | None, str | None]:
        """Initie l'upload et retourne (upload_url, video_id, error)."""
        size = video.stat().st_size
        url = f"{cfg.GRAPH_BASE}/{page_id}/videos"
        try:
            resp = requests.post(url, data={
                "access_token":    page_token,
                "upload_phase":    "start",
                "file_size":       str(size),
            }, timeout=30)
            data = resp.json()
        except Exception as e:
            return None, None, str(e)

        err = self._graph_error(data)
        if err:
            logger.error("Facebook init upload error: %s | %s", err, data)
            return None, None, err

        upload_url = data.get("upload_url") or data.get("upload_session_id")
        video_id   = data.get("video_id")
        if not upload_url:
            return None, None, f"Réponse init inattendue : {data}"
        return upload_url, video_id, None

    def _transfer_file(self, upload_url: str, page_token: str,
                       video: Path, on_log=None) -> tuple[bool, str | None, str | None]:
        """Envoie le fichier par tranches via PUT.

        Retourne (ok, error, video_id_from_transfer).
        Le video_id peut être renvoyé par l'API dans la dernière réponse.
        """
        size = video.stat().st_size
        mb_total = size / 1_048_576
        offset = 0
        last_video_id = None

        if not upload_url.startswith("http"):
            upload_url = f"{cfg.GRAPH_BASE}/{upload_url}"

        with video.open("rb") as f:
            while offset < size:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {
                    "Authorization":  f"OAuth {page_token}",
                    "Content-Type":   "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range":  f"bytes {offset}-{end}/{size}",
                }
                try:
                    resp = requests.put(upload_url, headers=headers,
                                        data=chunk, timeout=300)
                except Exception as e:
                    return False, str(e), None

                if resp.status_code not in (200, 201, 206):
                    return False, self._http_error(resp), None

                # Certaines réponses (dernière tranche) contiennent le video_id
                try:
                    body = resp.json()
                    if body.get("video_id"):
                        last_video_id = body["video_id"]
                except Exception:
                    pass

                offset += len(chunk)
                pct = int(offset / size * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                if on_log:
                    on_log(f"  ↑ Upload [{bar}] {pct}%  "
                           f"({offset/1_048_576:.0f}/{mb_total:.0f} Mo)")
        return True, None, last_video_id

    def _finish_upload(self, page_token: str, page_id: str,
                       video_id: str, caption: str) -> tuple[bool, str | None]:
        """Phase 'finish' : publie la vidéo uploadée."""
        url = f"{cfg.GRAPH_BASE}/{page_id}/videos"
        priv = cfg.privacy(self.config)
        try:
            resp = requests.post(url, data={
                "access_token":  page_token,
                "upload_phase":  "finish",
                "video_id":      video_id,
                "title":         (caption or "")[:254],
                "description":   (caption or "")[:2000],
                "privacy":       f'{{"value":"{priv}"}}',
                "published":     "true",
            }, timeout=30)
            data = resp.json()
        except Exception as e:
            return False, str(e)

        err = self._graph_error(data)
        if err:
            logger.error("Facebook finish error: %s | %s", err, data)
            return False, err
        logger.info("Vidéo publiée sur Page « %s » (%s).",
                    self.account_name, page_id)
        return True, None

    def _publish(self, page_token: str, page_id: str,
                 video: Path, caption: str, on_log=None) -> tuple[bool, str | None]:
        size = video.stat().st_size
        if on_log:
            on_log(f"  Initialisation upload Facebook ({size/1_048_576:.0f} Mo)…")

        upload_url, video_id, err = self._init_upload(
            page_token, page_id, video, caption)
        if err:
            return False, err

        ok, err, transfer_video_id = self._transfer_file(
            upload_url, page_token, video, on_log=on_log)
        if not ok:
            return False, err or "Transfert fichier échoué"

        # Le video_id peut venir de l'init OU de la dernière réponse du transfer
        final_video_id = video_id or transfer_video_id
        if not final_video_id:
            return False, "video_id introuvable dans la réponse Facebook."

        if on_log:
            on_log("  Finalisation de la publication…")
        return self._finish_upload(page_token, page_id, final_video_id, caption)

    # ── Point d'entrée ──────────────────────────────────────────────────

    def upload(self, credentials: dict, video_path: str,
               caption: str, on_log=None) -> tuple[bool, str | None]:
        video = Path(video_path)
        if not video.exists():
            logger.error("Vidéo introuvable : %s", video)
            return False, "Fichier vidéo introuvable"

        if cfg.simulate(self.config):
            return self._simulate(video, caption), None

        page_token = credentials.get("page_access_token")
        page_id    = credentials.get("page_id")
        if not page_token or not page_id:
            return False, "Compte non lié (page_access_token manquant)."

        try:
            return self._publish(page_token, page_id, video, caption, on_log=on_log)
        except Exception as e:
            logger.error("Erreur upload Facebook (%s) : %s", self.account_name, e)
            return False, str(e)

    def _simulate(self, video: Path, caption: str) -> bool:
        dest = app_data_dir() / "simulated_posts" / f"facebook_{self.account_name}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(video, dest / video.name)
            (dest / (video.stem + ".caption.txt")).write_text(caption or "", encoding="utf-8")
        except OSError as e:
            logger.warning("[SIMULATION] copie échouée : %s", e)
        logger.info("[SIMULATION] Vidéo « %s » publiée sur « %s » → %s",
                    video.name, self.account_name, dest)
        return True
