#!/usr/bin/env python3
"""ContentHub — plateforme locale d'automatisation de contenu multi-réseaux.

Point d'entrée : initialise le logging, charge l'environnement, découvre les
plugins réseaux et lance l'interface PySide6. 100 % local, aucun serveur.

Lancement :  python contenthub/app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Permet les imports `core...`, `networks...`, `ui...` quel que soit le cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Force l'UTF-8 pour les logs console sous Windows (emoji, accents).
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def main() -> int:
    from dotenv import load_dotenv
    from core.paths import env_path, app_data_dir

    # 1. .env dans contenthub_data/ (persist entre rebuilds — clés API, etc.)
    persistent_env = app_data_dir() / ".env"
    if persistent_env.exists():
        load_dotenv(persistent_env, override=False)

    # 2. .env legacy dans app_data_dir() (même chemin, rétrocompat)
    if env_path().exists() and env_path() != persistent_env:
        load_dotenv(env_path(), override=False)

    from core.logbus import get_bus
    get_bus()  # branche le bus de logs

    from core.registry import get_plugins
    get_plugins()  # découvre et enregistre les réseaux

    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from ui.theme import qss
    from ui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("ContentHub")
    app.setStyleSheet(qss())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
