#!/usr/bin/env python3
"""Démarre le tunnel ngrok (URL fixe avec authtoken) et l'écrit dans la config.

ngrok avec un compte gratuit donne une URL de domaine RESERVE stable
(untrue-resigned-panoramic.ngrok-free.dev) qui survit aux redémarrages —
contrairement aux Quick Tunnels Cloudflare qui changent à chaque lancement.

L'environnement cloud Claude (réglé sur "Full" network access) peut joindre
ngrok directement, à condition d'envoyer le header `ngrok-skip-browser-warning`.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.paths import app_data_dir, data_dir

CONFIG_FILE = app_data_dir() / "routine_config.json"
NGROK_DIR = data_dir() / "ngrok"
NGROK_EXE = NGROK_DIR / "ngrok.exe"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def find_ngrok() -> str | None:
    """Locate ngrok.exe — bundled dir first, then PATH."""
    if NGROK_EXE.exists():
        return str(NGROK_EXE)
    try:
        subprocess.run(["ngrok", "version"], capture_output=True, check=True)
        return "ngrok"
    except Exception:
        return None


def get_tunnel_url() -> str | None:
    """Query ngrok's local API for the active https tunnel URL."""
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=5) as r:
            data = json.loads(r.read())
        for t in data.get("tunnels", []):
            if t.get("proto") == "https":
                return t.get("public_url")
    except Exception:
        pass
    return None


def main():
    ngrok = find_ngrok()
    if not ngrok:
        logger.error("ngrok introuvable. Lancer setup_ngrok.py d'abord.")
        sys.exit(1)

    # Si un tunnel tourne déjà, réutilise son URL.
    existing = get_tunnel_url()
    if existing:
        logger.info(f"Tunnel ngrok déjà actif : {existing}")
        cfg = load_config()
        cfg["ngrok_url"] = existing
        cfg["cloudflare_url"] = ""
        save_config(cfg)
        return

    if sys.platform == "win32":
        proc = subprocess.Popen(
            [ngrok, "http", "5050", "--log=stdout"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        proc = subprocess.Popen(
            [ngrok, "http", "5050", "--log=stdout"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    # Attends l'URL via l'API locale ngrok.
    url = None
    for _ in range(30):
        time.sleep(1)
        url = get_tunnel_url()
        if url:
            break

    if not url:
        logger.error("Impossible de récupérer l'URL ngrok")
        proc.terminate()
        sys.exit(1)

    cfg = load_config()
    cfg["ngrok_url"] = url
    cfg["cloudflare_url"] = ""
    save_config(cfg)
    logger.info(f"✓ Tunnel ngrok actif : {url}")

    proc.wait()


if __name__ == "__main__":
    main()
