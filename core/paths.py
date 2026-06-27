"""Résolution des chemins, compatible exécutable figé (PyInstaller).

- data_dir()   : dossier INSCRIPTIBLE (base de données, .env perso, output/).
- bundle_dir() : ressources EMBARQUÉES en lecture seule (generator/, resources/).

Nouvelle arborescence (contenthub/ est la racine autonome) :

    contenthub/
      app.py  core/  networks/  ui/
      generator/      ← outil TTS local (ex-manual/src)
      resources/      ← assets/ ffmpeg/ models/ partagés
      data/           ← output/, contenthub_data/ (db, .env) — inscriptible

En mode dev, bundle_dir() = contenthub/ et data_dir() = contenthub/data/.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _frozen() -> bool:
    return getattr(sys, "frozen", False)


def _project_root() -> Path:
    # contenthub/core/paths.py -> remonte à contenthub/
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Dossier inscriptible.

    En mode figé (exe) : %APPDATA%/ContentHub/ — PERSISTANT entre rebuilds,
    jamais écrasé par PyInstaller. DB, .env, clés API et vidéos y survivent.
    En mode dev : contenthub/data/.
    """
    if _frozen():
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = Path(appdata) / "ContentHub"
        d.mkdir(parents=True, exist_ok=True)
        return d
    d = _project_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundle_dir() -> Path:
    """Ressources embarquées (lecture seule). _MEIPASS en figé, sinon racine."""
    if _frozen():
        # _MEIPASS : répertoire d'extraction PyInstaller (contient _internal).
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # Fallback : _internal/ à côté de l'exe.
        exe_dir = Path(sys.executable).parent
        internal = exe_dir / "_internal"
        if internal.exists():
            return internal
        return exe_dir
    return _project_root()


def app_data_dir() -> Path:
    """Sous-dossier inscriptible dédié à la plateforme (db, config, état)."""
    d = data_dir() / "contenthub_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir() -> Path:
    """Dossier où l'outil local de génération écrit les vidéos prêtes."""
    return data_dir() / "output"


def db_path() -> Path:
    return app_data_dir() / "contenthub.db"


def env_path() -> Path:
    return app_data_dir() / ".env"
