"""Pont vers l'outil de génération local (contenthub/generator/main.py).

On lance le pipeline en SOUS-PROCESSUS, jamais en import : isolation totale,
generator/ et ses lourdes dépendances (torch/whisper) ne polluent pas le process
de la plateforme. La sortie est diffusée ligne par ligne.
"""

from __future__ import annotations

import os
import sys
import shutil
import logging
import subprocess
from pathlib import Path

from .paths import data_dir, bundle_dir, output_dir

logger = logging.getLogger(__name__)

MAIN_SCRIPT = bundle_dir() / "generator" / "main.py"


def is_available() -> bool:
    return MAIN_SCRIPT.exists()


def _python_exe() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    for cand in ("python", "python3", "py"):
        found = shutil.which(cand)
        if found:
            return found
    return "python"


def _resources_dir() -> Path:
    return bundle_dir() / "resources"


def _assets_dir() -> Path:
    # Priorité à un dossier assets inscriptible (data/assets), sinon resources.
    candidate = data_dir() / "assets"
    if candidate.exists() and any(candidate.glob("*.mp4")):
        return candidate
    return _resources_dir() / "assets"


def _ffmpeg_path() -> str:
    for base in (_resources_dir(), data_dir(), bundle_dir()):
        cand = base / "ffmpeg" / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    return shutil.which("ffmpeg") or "ffmpeg"


def generate(count: int, content_type: str | None = None,
             on_log=None, stop_check=None) -> bool:
    """Génère `count` vidéos verticales uniques via l'outil local.

    Retourne True si le sous-processus se termine avec le code 0. Les vidéos sont
    écrites dans output/videos/ ; la circulation (cf. core.campaign) en assigne
    ensuite une distincte par compte.
    """
    if count < 1:
        raise ValueError("count doit être >= 1")
    if not MAIN_SCRIPT.exists():
        raise FileNotFoundError(
            f"Outil de génération introuvable : {MAIN_SCRIPT}. "
            "Le dossier « manual/ » doit être présent.")

    cmd = [_python_exe(), str(MAIN_SCRIPT), str(count)]
    if content_type:
        cmd += ["--type", content_type]

    def log(m):
        logger.info(m)
        if on_log:
            on_log(m)

    log(f"$ {' '.join(cmd)}")
    env = os.environ.copy()
    env["TIKTOK_ASSETS_DIR"] = str(_assets_dir())
    env["TIKTOK_OUTPUT_DIR"] = str(output_dir())
    env["FFMPEG_PATH"] = _ffmpeg_path()
    env["WHISPER_CACHE_DIR"] = str(_resources_dir() / "models")

    # CREATE_NO_WINDOW : empêche l'ouverture d'une fenêtre console Windows
    # pour le sous-processus de génération (sinon un terminal surgit à chaque run).
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        cmd, cwd=str(data_dir()), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=creationflags)

    try:
        for line in proc.stdout:
            if stop_check and stop_check():
                proc.terminate()
                log("⏹ Génération interrompue.")
                proc.wait(timeout=10)
                return False
            line = line.rstrip("\n")
            if line:
                log(line)
    finally:
        proc.stdout.close()

    code = proc.wait()
    if code != 0:
        logger.error("Génération échouée (code %s)", code)
    return code == 0
