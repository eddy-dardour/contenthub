"""Logos officiels des réseaux pour l'UI.

Chaque plugin expose `icon` = une CLÉ de logo (« tiktok », « youtube », « x »).
Ce module résout cette clé vers le vrai logo de la marque (SVG embarqué) sous
forme de QPixmap/QIcon. Pour les contextes purement texte (items de QComboBox),
`glyph()` fournit un repli emoji/caractère.

Compatible exécutable figé (PyInstaller) : les SVG sont cherchés à côté du
package, ou dans _MEIPASS.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QIcon, QPainter
from PySide6.QtSvg import QSvgRenderer


# Repli texte quand on ne peut pas afficher d'image (listes déroulantes).
_GLYPHS = {
    "tiktok":   "♪",
    "youtube":  "▶",
    "x":        "𝕏",
    "facebook": "f",
}


def _logos_dir() -> Path:
    # Src : fichiers locaux (dev).
    here = Path(__file__).resolve().parent / "assets" / "logos"
    if here.exists():
        return here
    # Mode figé (PyInstaller) : _MEIPASS/ui/assets/logos (cf. spec datas).
    base = Path(getattr(sys, "_MEIPASS", None))
    if base:
        candidate = base / "ui" / "assets" / "logos"
        if candidate.exists():
            return candidate
    # Fallback : à côté de l'exe (dist/).
    base = Path(sys.executable).parent
    candidate = base / "ui" / "assets" / "logos"
    if candidate.exists():
        return candidate
    # Dernier recours : racine de l'exe.
    return base / "assets" / "logos"


def glyph(key: str) -> str:
    """Repli caractère pour les contextes texte (QComboBox, tableaux)."""
    return _GLYPHS.get(key, "●")


@lru_cache(maxsize=64)
def pixmap(key: str, size: int = 24) -> QPixmap:
    """Logo de la marque en QPixmap carré `size`x`size` (rendu net via SVG).

    Retourne un pixmap vide si le logo est introuvable (l'appelant peut alors
    retomber sur glyph()).
    """
    svg = _logos_dir() / f"{key}.svg"
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    if not svg.exists():
        return pm
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        return pm
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return pm


def icon(key: str, size: int = 24) -> QIcon:
    """Logo de la marque en QIcon (pour QComboBox.addItem, boutons…)."""
    pm = pixmap(key, size)
    return QIcon(pm) if not pm.isNull() else QIcon()


def has_logo(key: str) -> bool:
    return (_logos_dir() / f"{key}.svg").exists()
