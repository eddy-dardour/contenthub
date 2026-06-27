"""Découverte du contenu prêt à publier.

L'outil de génération local écrit ses vidéos dans output/videos/ et les
légendes dans output/_TIKTOK_CAPTIONS.txt (format hérité). Ce module fournit
une vue agnostique : une liste de ContentItem(key, path, caption).
"""

from __future__ import annotations

import re
import logging
from pathlib import Path

from .paths import output_dir
from .models import ContentItem

logger = logging.getLogger(__name__)

DEFAULT_CAPTION = "Check this out!"

# Nom de fichier `<story>_<part>.mp4` (ex: 002_01.mp4). Le préfixe regroupe les
# parties d'une même histoire ; le suffixe numérique donne l'ordre de publication.
_STORY_RE = re.compile(r"^(?P<story>.+?)_(?P<part>\d+)$")


def _parse_story(stem: str) -> tuple[str, int]:
    """Extrait (story_key, part) du nom de fichier sans extension."""
    m = _STORY_RE.match(stem)
    if m:
        return m.group("story"), int(m.group("part"))
    return stem, 1


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
        story, part = _parse_story(p.stem)
        items.append(ContentItem(
            key=p.name,
            path=str(p),
            caption=captions.get(p.name, DEFAULT_CAPTION),
            size_bytes=p.stat().st_size,
            story_key=story,
            part=part,
        ))
    return items


def group_by_story(items: list[ContentItem] | None = None) -> list[list[ContentItem]]:
    """Regroupe les vidéos par histoire, parties triées par ordre de publication.

    Retourne une liste de groupes ; chaque groupe = les parties d'une histoire
    (1 élément si l'histoire n'a qu'une partie). Les groupes sont ordonnés par
    story_key pour un comportement déterministe.
    """
    if items is None:
        items = list_content()
    groups: dict[str, list[ContentItem]] = {}
    for it in items:
        groups.setdefault(it.story_key, []).append(it)
    ordered = []
    for story in sorted(groups):
        parts = sorted(groups[story], key=lambda x: x.part)
        ordered.append(parts)
    return ordered


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
