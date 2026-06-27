"""Upload via l'API officielle TikTok Content Posting (gratuite, 0 shadowban).

Publie directement en SELF_ONLY (privé). La vidéo est visible uniquement par
le compte TikTok connecté — pas de brouillon, pas d'audit requis en sandbox.

En mode SIMULATION, la vidéo est copiée dans contenthub_data/simulated_posts/.
"""

from __future__ import annotations

import math
import random
import time
import shutil
import logging
from pathlib import Path

import requests

from core.paths import app_data_dir
from . import config as cfg

logger = logging.getLogger(__name__)

API = "https://open.tiktokapis.com/v2"
DIRECT_INIT = f"{API}/post/publish/video/init/"
CREATOR_INFO = f"{API}/post/publish/creator_info/query/"
STATUS_FETCH = f"{API}/post/publish/status/fetch/"


class Uploader:
    def __init__(self, config: dict, account_name: str = ""):
        self.config = config
        self.account_name = account_name or "compte"
        self.privacy = cfg.privacy_level(config)

    # ── HTTP helpers ────────────────────────────────────────────────────

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8"}

    def _post(self, url: str, token: str, payload: dict) -> dict:
        resp = requests.post(url, headers=self._headers(token), json=payload, timeout=30)
        try:
            return resp.json()
        except Exception:
            return {"_http": resp.status_code, "_raw": resp.text}

    @staticmethod
    def _err(data: dict) -> str | None:
        err = (data or {}).get("error") or {}
        code = err.get("code")
        if code and code != "ok":
            return err.get("message") or code
        return None

    @staticmethod
    def _chunk_params(size: int) -> tuple[int, int]:
        MIN = 5 * 1024 * 1024
        SINGLE_MAX = 64 * 1024 * 1024
        if size <= SINGLE_MAX:
            return max(size, MIN), 1
        count = math.ceil(size / SINGLE_MAX)
        chunk_size = size // count
        return chunk_size, count

    def _put_file(self, upload_url: str, video: Path, on_log=None) -> bool:
        size = video.stat().st_size
        chunk_size, count = self._chunk_params(size)
        mb_total = size / 1_048_576
        offset = 0
        with video.open("rb") as f:
            for idx in range(count):
                chunk = f.read() if idx == count - 1 else f.read(chunk_size)
                if not chunk:
                    break
                end = offset + len(chunk) - 1
                pct = int((offset + len(chunk)) / size * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                if on_log:
                    on_log(f"  ↑ Upload [{bar}] {pct}%  ({(offset+len(chunk))/1_048_576:.0f}/{mb_total:.0f} Mo)")
                headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{end}/{size}",
                }
                resp = requests.put(upload_url, headers=headers, data=chunk, timeout=300)
                if resp.status_code not in (200, 201, 206):
                    logger.error("Chunk %d upload échoué (HTTP %s) : %s",
                                 idx, resp.status_code, resp.text[:200])
                    return False
                offset += len(chunk)
                # Micro-pause inter-chunk : simule une connexion réseau réelle (jitter naturel).
                if idx < count - 1:
                    time.sleep(random.uniform(0.1, 0.6))
        return True

    def _wait(self, token: str, publish_id: str, timeout_s: int = 300, on_log=None) -> bool:
        deadline = time.time() + timeout_s
        ok_states = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"}
        elapsed = 0
        while time.time() < deadline:
            data = self._post(STATUS_FETCH, token, {"publish_id": publish_id})
            if self._err(data):
                logger.error("status/fetch : %s", self._err(data))
                return False
            status = (data.get("data") or {}).get("status")
            if status in ok_states:
                return True
            if status in ("FAILED", "CANCELED"):
                reason = (data.get("data") or {}).get("fail_reason", "")
                logger.error("Publication %s : %s", status, reason)
                return False
            elapsed += 3
            if on_log:
                on_log(f"  ⏳ Traitement TikTok… ({elapsed}s)")
            time.sleep(3)
        logger.warning("Statut de publication non confirmé (timeout).")
        return False

    def _resolve_privacy(self, token: str) -> str:
        info = self._post(CREATOR_INFO, token, {})
        if self._err(info):
            return self.privacy
        options = (info.get("data") or {}).get("privacy_level_options") or []
        if not options or self.privacy in options:
            return self.privacy
        return "SELF_ONLY" if "SELF_ONLY" in options else options[0]

    def _publish(self, token: str, video: Path, caption: str, on_log=None) -> tuple[bool, str | None]:
        privacy = self._resolve_privacy(token)
        size = video.stat().st_size
        chunk_size, count = self._chunk_params(size)

        # Délai pré-upload aléatoire : simule un humain qui ouvre l'app, hésite,
        # puis poste — évite la signature "init immédiatement après auth".
        pre_delay = random.uniform(4.0, 18.0)
        if on_log:
            on_log(f"  Préparation upload ({size/1_048_576:.0f} Mo, {count} chunk(s))… ({pre_delay:.0f}s)")
        time.sleep(pre_delay)

        data = self._post(DIRECT_INIT, token, {
            "post_info": {
                "title": (caption or "")[:2200],
                "privacy_level": privacy,
                "disable_comment": False,
                "disable_duet": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD", "video_size": size,
                "chunk_size": chunk_size, "total_chunk_count": count},
        })
        if self._err(data):
            err = self._err(data)
            logger.error("video/init error=%s | full=%s", err, data)
            return False, err
        d = data.get("data") or {}
        if not (d.get("publish_id") and d.get("upload_url")):
            logger.error("Réponse init inattendue : %s", data)
            return False, f"Réponse inattendue : {data}"
        # Pause entre l'init et le premier chunk : simule le temps de chargement
        # de l'interface créateur avant que l'utilisateur confirme l'envoi.
        time.sleep(random.uniform(1.5, 4.0))

        if not self._put_file(d["upload_url"], video, on_log=on_log):
            return False, "Upload fichier échoué"
        ok = self._wait(token, d["publish_id"], on_log=on_log)
        if ok:
            logger.info("Vidéo publiée (%s) sur « %s ».", privacy, self.account_name)
            return True, None
        return False, "Timeout statut publication"

    # ── Point d'entrée ──────────────────────────────────────────────────

    def upload(self, token: str, video_path: str, caption: str, on_log=None) -> tuple[bool, str | None]:
        video = Path(video_path)
        if not video.exists():
            logger.error("Vidéo introuvable : %s", video)
            return False, "Fichier vidéo introuvable"
        if cfg.simulate(self.config):
            return self._simulate(video, caption), None
        try:
            return self._publish(token, video, caption, on_log=on_log)
        except Exception as e:
            logger.error("Erreur upload (%s) : %s", self.account_name, e)
            return False, str(e)

    def _simulate(self, video: Path, caption: str) -> bool:
        dest = app_data_dir() / "simulated_posts" / self.account_name
        dest.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(video, dest / video.name)
            (dest / (video.stem + ".caption.txt")).write_text(caption or "", encoding="utf-8")
        except OSError as e:
            logger.warning("[SIMULATION] copie échouée : %s", e)
        logger.info("[SIMULATION] « %s » publié (%s) sur « %s » → %s",
                    video.name, self.privacy, self.account_name, dest)
        time.sleep(0.4)
        return True
