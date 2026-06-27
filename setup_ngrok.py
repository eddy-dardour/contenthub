#!/usr/bin/env python3
"""Setup ngrok tunnel pour exposer l'API ContentHub en local à Internet.

Usage:
    python setup_ngrok.py --authtoken <ton_token_ngrok>

Cela :
  1. Télécharge ngrok si absent
  2. Configure ngrok avec le token (compte gratuit = URL statique)
  3. Lance ngrok http 5050 et capture l'URL publique
  4. Écrit l'URL dans routine_config.json["ngrok_url"]
  5. Garde le tunnel actif en background
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import argparse
import requests
from pathlib import Path
from urllib.request import urlopen

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.paths import app_data_dir, data_dir

_DATA_DIR = data_dir()
CONFIG_FILE = app_data_dir() / "routine_config.json"
NGROK_DIR = _DATA_DIR / "ngrok"
NGROK_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def download_ngrok() -> Path:
    """Download ngrok binary if not present."""
    import platform

    system = platform.system()
    arch = platform.machine()

    # Determine ngrok binary name
    if system == "Windows":
        filename = "ngrok.exe"
        url_suffix = "windows-amd64"
    elif system == "Darwin":
        url_suffix = "darwin-amd64" if arch == "x86_64" else "darwin-arm64"
        filename = "ngrok"
    else:  # Linux
        url_suffix = "linux-amd64" if arch == "x86_64" else "linux-arm64"
        filename = "ngrok"

    ngrok_path = NGROK_DIR / filename
    if ngrok_path.exists():
        logger.info(f"ngrok binary found at {ngrok_path}")
        return ngrok_path

    logger.info(f"Downloading ngrok for {system} ({arch})...")
    url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-{url_suffix}.zip"

    try:
        import zipfile

        zip_path = NGROK_DIR / "ngrok.zip"
        with urlopen(url) as response:
            zip_path.write_bytes(response.read())

        with zipfile.ZipFile(zip_path) as z:
            z.extractall(NGROK_DIR)

        zip_path.unlink()
        logger.info(f"ngrok downloaded to {ngrok_path}")
        return ngrok_path
    except Exception as e:
        logger.error(f"Failed to download ngrok: {e}")
        raise


def setup_ngrok_auth(ngrok_exe: Path, authtoken: str) -> None:
    """Configure ngrok with auth token."""
    logger.info(f"Configuring ngrok with token...")
    try:
        subprocess.run(
            [str(ngrok_exe), "config", "add-authtoken", authtoken],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("ngrok auth token configured")
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to configure ngrok: {e}")
        raise


def get_ngrok_public_url(max_retries: int = 30) -> str | None:
    """Get public URL from ngrok's local API (http://127.0.0.1:4040)."""
    for attempt in range(max_retries):
        try:
            response = requests.get("http://127.0.0.1:4040/api/tunnels", timeout=2)
            data = response.json()
            tunnels = data.get("tunnels", [])
            for tunnel in tunnels:
                if tunnel.get("proto") == "https":
                    return tunnel["public_url"]
        except Exception:
            pass

        if attempt < max_retries - 1:
            logger.info(f"Waiting for ngrok tunnel... ({attempt + 1}/{max_retries})")
            time.sleep(1)

    return None


def launch_ngrok_tunnel(ngrok_exe: Path) -> subprocess.Popen:
    """Launch ngrok http 5050 in background."""
    logger.info("Launching ngrok tunnel on port 5050...")

    if sys.platform == "win32":
        proc = subprocess.Popen(
            [str(ngrok_exe), "http", "5050"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        proc = subprocess.Popen(
            [str(ngrok_exe), "http", "5050"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    logger.info(f"ngrok process started (PID: {proc.pid})")
    return proc


def main():
    parser = argparse.ArgumentParser(description="Setup ngrok tunnel for ContentHub API")
    parser.add_argument(
        "--authtoken",
        required=True,
        help="ngrok auth token (from https://dashboard.ngrok.com/get-started/your-authtoken)",
    )
    args = parser.parse_args()

    try:
        # Step 1: Download ngrok if needed
        ngrok_exe = download_ngrok()

        # Step 2: Configure ngrok auth
        setup_ngrok_auth(ngrok_exe, args.authtoken)

        # Step 3: Launch ngrok tunnel
        proc = launch_ngrok_tunnel(ngrok_exe)

        # Step 4: Get public URL
        public_url = get_ngrok_public_url()
        if not public_url:
            logger.error("Failed to obtain ngrok public URL")
            sys.exit(1)

        logger.info(f"✓ ngrok tunnel active at: {public_url}")

        # Step 5: Save to config
        cfg = load_config()
        cfg["ngrok_url"] = public_url
        save_config(cfg)
        logger.info(f"✓ ngrok_url saved to config")

        logger.info("\n" + "=" * 70)
        logger.info("ngrok tunnel is running. Your API is now publicly accessible at:")
        logger.info(f"  {public_url}")
        logger.info("\nUse this URL in Claude Routine:")
        logger.info(f"  GET  {public_url}/health")
        logger.info(f"  POST {public_url}/run")
        logger.info("\nPress Ctrl+C to stop the tunnel.")
        logger.info("=" * 70 + "\n")

        # Keep the process running
        proc.wait()

    except KeyboardInterrupt:
        logger.info("\nTunnel closed by user")
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
