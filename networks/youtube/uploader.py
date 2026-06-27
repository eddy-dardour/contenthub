"""Upload via l'API officielle YouTube Data v3 (gratuite, quota inclus).

Publie une vidéo verticale (< 60 s) qui sera automatiquement classée comme
YouTube Short. Utilise l'upload « resumable » (en deux temps : session + envoi
du fichier), en pur `requests` — aucune dépendance Google lourde.

En mode SIMULATION, la vidéo est copiée dans contenthub_data/simulated_posts/.
"""

from __future__ import annotations

import json
import shutil
import logging
from pathlib import Path

import requests

from core.paths import app_data_dir
from . import config as cfg

logger = logging.getLogger(__name__)

# Catégorie 24 = « Entertainment » (valeur sûre et universelle).
DEFAULT_CATEGORY_ID = "24"
CHUNK = 8 * 1024 * 1024  # 8 Mo par requête PUT


class Uploader:
    def __init__(self, config: dict, account_name: str = ""):
        self.config = config
        self.account_name = account_name or "compte"
        self.privacy = cfg.privacy_status(config)

    # ── Métadonnées ─────────────────────────────────────────────────────

    @staticmethod
    def _split_caption(caption: str) -> tuple[str, str]:
        """Sépare titre (1re ligne) et description (reste) d'une légende.

        YouTube impose un titre <= 100 caractères. On garde la 1re ligne comme
        titre (tronquée) et l'ensemble comme description. On suffixe #Shorts pour
        renforcer le classement en Short.
        """
        caption = (caption or "").strip() or "Story"
        first = caption.split("\n", 1)[0].strip()
        title = (first[:97] + "…") if len(first) > 100 else first
        if "#shorts" not in caption.lower():
            description = f"{caption}\n\n#Shorts"
        else:
            description = caption
        return title, description[:4900]

    def _metadata(self, caption: str) -> dict:
        title, description = self._split_caption(caption)
        return {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": DEFAULT_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": self.privacy,
                "selfDeclaredMadeForKids": cfg.made_for_kids(self.config),
            },
        }

    # ── Upload resumable ────────────────────────────────────────────────

    def _start_session(self, token: str, video: Path, metadata: dict) -> tuple[str | None, str | None]:
        size = video.stat().st_size
        params = {"uploadType": "resumable", "part": "snippet,status"}
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        }
        resp = requests.post(cfg.UPLOAD_URL, params=params, headers=headers,
                             data=json.dumps(metadata), timeout=30)
        if resp.status_code not in (200, 201):
            return None, self._http_error(resp)
        location = resp.headers.get("Location")
        if not location:
            return None, "Session d'upload non initialisée (Location manquante)."
        return location, None

    def _upload_file(self, session_url: str, video: Path, on_log=None) -> tuple[bool, str | None]:
        size = video.stat().st_size
        mb_total = size / 1_048_576
        offset = 0
        with video.open("rb") as f:
            while offset < size:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                }
                resp = requests.put(session_url, headers=headers, data=chunk, timeout=300)
                # 308 = en cours (chunk accepté) ; 200/201 = upload terminé.
                if resp.status_code in (200, 201):
                    offset = size
                elif resp.status_code == 308:
                    offset = end + 1
                else:
                    return False, self._http_error(resp)
                pct = int(offset / size * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                if on_log:
                    on_log(f"  ↑ Upload [{bar}] {pct}%  ({offset/1_048_576:.0f}/{mb_total:.0f} Mo)")
        return True, None

    @staticmethod
    def _http_error(resp) -> str:
        try:
            data = resp.json()
            err = (data.get("error") or {})
            msg = err.get("message") or json.dumps(err)
        except Exception:
            msg = resp.text[:200]
        return f"HTTP {resp.status_code} : {msg}"

    # ── Point d'entrée ──────────────────────────────────────────────────

    def upload(self, token: str, video_path: str, caption: str, on_log=None) -> tuple[bool, str | None]:
        video = Path(video_path)
        if not video.exists():
            logger.error("Vidéo introuvable : %s", video)
            return False, "Fichier vidéo introuvable"
        if cfg.simulate(self.config):
            return self._simulate(video, caption), None
        try:
            metadata = self._metadata(caption)
            if on_log:
                on_log(f"  Initialisation upload YouTube ({video.stat().st_size/1_048_576:.0f} Mo)…")
            session_url, err = self._start_session(token, video, metadata)
            if not session_url:
                logger.error("Session YouTube échouée : %s", err)
                return False, err
            ok, err = self._upload_file(session_url, video, on_log=on_log)
            if ok:
                logger.info("Short publié (%s) sur « %s ».", self.privacy, self.account_name)
                return True, None
            return False, err
        except Exception as e:
            logger.error("Erreur upload YouTube (%s) : %s", self.account_name, e)
            return False, str(e)

    def _simulate(self, video: Path, caption: str) -> bool:
        dest = app_data_dir() / "simulated_posts" / f"youtube_{self.account_name}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(video, dest / video.name)
            (dest / (video.stem + ".caption.txt")).write_text(caption or "", encoding="utf-8")
        except OSError as e:
            logger.warning("[SIMULATION] copie échouée : %s", e)
        logger.info("[SIMULATION] Short « %s » publié (%s) sur « %s » → %s",
                    video.name, self.privacy, self.account_name, dest)
        return True
