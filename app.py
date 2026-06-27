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


def _launch_background(script: Path, extra_args: list[str] | None = None) -> None:
    """Lance un script Python en background sans console."""
    import subprocess
    cmd = [sys.executable, str(script)] + (extra_args or [])
    if sys.platform == "win32":
        subprocess.Popen(
            cmd, cwd=str(script.parent),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            cmd, cwd=str(script.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def _port_in_use(port: int) -> bool:
    """True si un service écoute déjà sur ce port local."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _start_background_services():
    """Lance api_server.py + tunnel ngrok au démarrage, SANS doublon.

    On vérifie d'abord si le port API (5050) est déjà occupé : si oui, un serveur
    tourne déjà (lancé par une instance précédente) et on ne relance rien — ce qui
    évite d'empiler des process/terminaux à chaque ouverture de l'app.
    """
    import threading

    root = Path(__file__).resolve().parent

    def launch_all():
        try:
            if _port_in_use(5050):
                return  # API (et donc tunnel) déjà en route — rien à relancer.

            api_script = root / "api_server.py"
            if api_script.exists():
                _launch_background(api_script)

            # Tunnel ngrok : URL fixe (untrue-resigned-panoramic.ngrok-free.dev),
            # stable entre redémarrages, joignable depuis le cloud Claude (Full).
            tunnel_script = root / "start_ngrok_tunnel.py"
            if tunnel_script.exists():
                _launch_background(tunnel_script)
        except Exception:
            pass  # never crash the UI

    threading.Thread(target=launch_all, daemon=True).start()


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

    _start_background_services()

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
