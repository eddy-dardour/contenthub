"""Découverte du contenu prêt à publier.

L'outil de génération local écrit ses vidéos dans output/videos/ et les
légendes dans output/_TIKTOK_CAPTIONS.txt (format hérité). Ce module fournit
une vue agnostique : une liste de ContentItem(key, path, caption).
"""

from __future__ import annotations

import logging
from pathlib import Path

from .paths import output_dir
from .models import ContentItem

logger = logging.getLogger(__name__)

DEFAULT_CAPTION = "Check this out!"


def _videos_dir() -> Path:
    return output_dir() / "videos"


def _captions_file() -> Path:
    return output_dir() / "_TIKTOK_CAPTIONS.txt"


def read_captions() -> dict[str, str]:
    """Parse output/_TIKTOK_CAPTIONS.txt → {'001_01.mp4': 'caption', ...}.

    Un en-tête `### <nom>` ouvre un bloc ; `---`/`===` le ferme.
    """
    f = _captions_file()
    captions: dict[str, str] = {}
    if not f.exists():
        return captions
    try:
        content = f.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Lecture des légendes échouée : %s", e)
        return captions

    name, lines = None, []

    def flush():
        if name and lines:
            captions[name] = "\n".join(lines).strip()

    for line in content.split("\n"):
        if line.startswith("### "):
            flush()
            name, lines = line[4:].strip(), []
        elif line.startswith("---") or line.startswith("==="):
            flush()
            name, lines = None, []
        elif name is not None:
            lines.append(line)
    flush()
    return captions


def list_content() -> list[ContentItem]:
    vdir = _videos_dir()
    if not vdir.exists():
        return []
    captions = read_captions()
    items = []
    for p in sorted(vdir.glob("*.mp4")):
        items.append(ContentItem(
            key=p.name,
            path=str(p),
            caption=captions.get(p.name, DEFAULT_CAPTION),
            size_bytes=p.stat().st_size,
        ))
    return items


def get_content(key: str) -> ContentItem | None:
    for item in list_content():
        if item.key == key:
            return item
    return None


def remove_content(key: str) -> bool:
    """Supprime le fichier vidéo après publication réussie."""
    p = _videos_dir() / key
    try:
        if p.exists():
            p.unlink()
            logger.info("Vidéo supprimée après publication : %s", key)
            return True
    except OSError as e:
        logger.warning("Impossible de supprimer %s : %s", key, e)
    return False
