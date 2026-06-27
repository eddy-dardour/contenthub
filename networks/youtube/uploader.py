"""Upload via l'API officielle YouTube Data v3 (gratuite, quota inclus).

Publie une vidéo verticale (< 60 s) qui sera automatiquement classée comme
YouTube Short. Utilise l'upload « resumable » (en deux temps : session + envoi
du fichier), en pur `requests` — aucune dépendance Google lourde.

En mode SIMULATION, la vidéo est copiée dans contenthub_data/simulated_posts/.
"""

from __future__ import annotations

import json
import re
import shutil
import logging
from pathlib import Path

import requests

from core.paths import app_data_dir
from . import config as cfg

logger = logging.getLogger(__name__)

# Catégorie 24 = Entertainment — polyvalent, évite les mauvaises surprises algo.
DEFAULT_CATEGORY_ID = "24"
# Langue par défaut des métadonnées (snippet.defaultLanguage).
DEFAULT_LANGUAGE = "en"
CHUNK = 8 * 1024 * 1024  # 8 Mo par requête PUT

# Mots courts à ignorer lors de l'extraction de tags depuis le titre.
_STOPWORDS = {
    "le", "la", "les", "de", "du", "des", "un", "une", "en", "et", "à",
    "au", "aux", "il", "elle", "on", "ce", "qui", "que", "si", "ou",
    "the", "a", "an", "of", "in", "is", "to", "it", "for", "on", "at",
}


def _extract_tags(title: str, caption: str) -> list[str]:
    """Génère 5-8 tags SEO depuis le titre + hashtags présents dans la légende.

    Ordre de priorité : hashtags explicites → mots clés du titre → fallback Shorts.
    YouTube ignore les tags >500 chars total et >8 peu ciblés.
    """
    tags: list[str] = []

    # 1. Hashtags déjà écrits dans la légende (#mot).
    for m in re.findall(r"#(\w+)", caption):
        tag = m.lower()
        if tag not in ("shorts", "short") and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break

    # 2. Mots significatifs du titre (≥4 chars, hors stopwords).
    for word in re.findall(r"\b[a-zA-ZÀ-ÿ]{4,}\b", title):
        w = word.lower()
        if w not in _STOPWORDS and w not in tags:
            tags.append(w)
        if len(tags) >= 7:
            break

    # 3. Tag #Shorts en dernier — signal de classement Short obligatoire.
    tags.append("shorts")
    return tags[:8]


def _build_fields(caption: str) -> tuple[str, str, list[str]]:
    """Retourne (title, description, tags) optimisés pour l'algorithme YouTube.

    Règles appliquées :
    - Titre ≤ 60 chars (affichage mobile complet) avec mot-clé en tête.
    - Description : hook visible avant "Show More" (2 premières lignes)
      puis corps complet, puis hashtags en bas (#Shorts + niche).
    - Tags : 5-8 tags ciblés extraits du contenu.
    """
    caption = (caption or "").strip() or "Story"
    lines = caption.split("\n")
    first_line = lines[0].strip()

    # Titre : 60 chars max pour affichage mobile sans troncature.
    title = (first_line[:57] + "…") if len(first_line) > 60 else first_line

    # Hashtags à placer en fin de description (visibles, indexés).
    inline = [m for m in re.findall(r"#\w+", caption) if m.lower() not in ("#shorts",)]
    footer_tags = " ".join(inline[:4]) + " #Shorts" if inline else "#Shorts"

    # Corps : hook (1re ligne en gras via NBSP trick non dispo API, on la garde brute),
    # puis reste de la légende épuré, puis hashtags footer.
    body_lines = [l for l in lines[1:] if l.strip()] if len(lines) > 1 else []
    body = "\n".join(body_lines).strip()

    if body:
        description = f"{first_line}\n\n{body}\n\n{footer_tags}"
    else:
        description = f"{first_line}\n\n{footer_tags}"

    tags = _extract_tags(title, caption)
    return title, description[:4900], tags


class Uploader:
    def __init__(self, config: dict, account_name: str = ""):
        self.config = config
        self.account_name = account_name or "compte"
        self.privacy = cfg.privacy_status(config)

    # ── Métadonnées ─────────────────────────────────────────────────────

    def _metadata(self, caption: str) -> dict:
        title, description, tags = _build_fields(caption)
        return {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": DEFAULT_CATEGORY_ID,
                "defaultLanguage": DEFAULT_LANGUAGE,
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

    def _upload_file(self, session_url: str, video: Path,
                     on_log=None) -> tuple[bool, str | None, str | None]:
        """Envoie le fichier par chunks. Retourne (ok, erreur, video_id).

        La réponse finale 200/201 contient la ressource vidéo créée : on en
        extrait l'`id` YouTube (nécessaire pour récupérer vues/likes ensuite).
        """
        size = video.stat().st_size
        mb_total = size / 1_048_576
        offset = 0
        video_id = None
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
                    try:
                        video_id = (resp.json() or {}).get("id")
                    except Exception:
                        video_id = None
                elif resp.status_code == 308:
                    offset = end + 1
                else:
                    return False, self._http_error(resp), None
                pct = int(offset / size * 100)
                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                if on_log:
                    on_log(f"  ↑ Upload [{bar}] {pct}%  ({offset/1_048_576:.0f}/{mb_total:.0f} Mo)")
        return True, None, video_id

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

    def upload(self, token: str, video_path: str, caption: str,
               on_log=None) -> tuple[bool, str | None, str | None]:
        """Publie la vidéo. Retourne (ok, erreur, video_id YouTube)."""
        video = Path(video_path)
        if not video.exists():
            logger.error("Vidéo introuvable : %s", video)
            return False, "Fichier vidéo introuvable", None
        if cfg.simulate(self.config):
            return self._simulate(video, caption), None, None
        try:
            metadata = self._metadata(caption)
            if on_log:
                on_log(f"  Initialisation upload YouTube ({video.stat().st_size/1_048_576:.0f} Mo)…")
            session_url, err = self._start_session(token, video, metadata)
            if not session_url:
                logger.error("Session YouTube échouée : %s", err)
                return False, err, None
            ok, err, video_id = self._upload_file(session_url, video, on_log=on_log)
            if ok:
                logger.info("Short publié (%s) sur « %s » [id=%s].",
                            self.privacy, self.account_name, video_id)
                return True, None, video_id
            return False, err, None
        except Exception as e:
            logger.error("Erreur upload YouTube (%s) : %s", self.account_name, e)
            return False, str(e), None

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
