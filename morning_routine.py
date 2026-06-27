#!/usr/bin/env python3
"""Routine de publication ContentHub (déclenchée par la routine cloud Claude).

Le scheduling est géré par la routine cloud Claude (cron) qui appelle
`POST /run` ; ce script exécute une passe de campagne/distribution :
  1. Lit le mode + le type de contenu (env CONTENT_TYPE/MODE ou config).
  2. Lit les comptes actifs+liés depuis la DB.
  3. Génère 1 histoire par compte puis distribue (parties dans l'ordre).

Usage :
    python morning_routine.py --now    # exécute immédiatement
"""

from __future__ import annotations

import sys
import json
import logging
import argparse
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import os

# Point to the same data dir as the frozen exe so DB/config are shared.
if not os.environ.get("CONTENTHUB_DATA_DIR") and not getattr(sys, "frozen", False):
    _appdata = os.environ.get("APPDATA") or str(Path.home())
    os.environ["CONTENTHUB_DATA_DIR"] = str(Path(_appdata) / "ContentHub")

# Même résolution que le reste de la plateforme (respecte CONTENTHUB_DATA_DIR).
from core.paths import data_dir as _resolve_data_dir
_DATA_DIR = _resolve_data_dir()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            _DATA_DIR / "routine.log",
            encoding="utf-8", errors="replace"),
    ]
)
logger = logging.getLogger(__name__)

CONFIG_FILE = _DATA_DIR / "contenthub_data" / "routine_config.json"
DEFAULT_CONTENT_TYPE = "tts_drama"
DEFAULT_COOLDOWN_HOURS = 8.0


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "default_content_type": DEFAULT_CONTENT_TYPE,
        "enabled": True,
    }


def _resolve_params(cfg: dict) -> dict:
    """Résout les paramètres depuis l'environnement (CONTENT_TYPE, MODE) ou la config."""
    return {
        "content_type": os.environ.get("CONTENT_TYPE")
                        or cfg.get("default_content_type", DEFAULT_CONTENT_TYPE),
        "mode": os.environ.get("MODE", "campaign"),
    }


# ── Exécution de la campagne ─────────────────────────────────────────────────

def run_campaign(params: dict, cfg: dict) -> None:
    if not cfg.get("enabled", True):
        logger.info("Routine désactivée dans la config.")
        return

    content_type_id = params.get("content_type") or cfg.get("default_content_type", DEFAULT_CONTENT_TYPE)
    mode = params.get("mode", "campaign")
    logger.info("Routine : type=%s mode=%s", content_type_id, mode)

    from core.catalog import get_type
    from core.campaign import run as campaign_run, eligible_accounts
    from core.publisher import Publisher
    from core import generator as gen_mod
    from core import progress_state

    ct = get_type(content_type_id)
    if not ct:
        logger.error("Type de contenu introuvable : %s", content_type_id)
        return

    # Publie la progression dans le fichier partagé pour que l'app UI (process
    # distinct) puisse synchroniser sa barre, même quand la routine est déclenchée
    # par Claude via l'API.
    progress_state.start(label=f"Routine {ct.label} ({mode})")

    def log_cb(ev, data):
        msg = data.get("message") or data.get("error") or str(data)
        logger.info("[%s] %s", ev, msg)
        # Relaie les events de progression vers le fichier partagé.
        try:
            if ev == "step":
                progress_state.step(data.get("label", ""),
                                    data.get("index", 0), data.get("total", 1))
            elif ev == "progress":
                progress_state.progress(data.get("value", 0), data.get("maximum", 1))
            elif ev == "uploading":
                progress_state.step(
                    f"Upload {data.get('account','')} ({data.get('network','')})",
                    progress_state.read().get("value", 0),
                    progress_state.read().get("maximum", 1))
            elif ev == "needs_reauth":
                progress_state.add_reauth(data.get("account", ""))
            else:
                # Events "log"/"info"/"done" — pendant la génération vidéo (Whisper,
                # ffmpeg), chaque ligne de stdout remet à jour updated_at et empêche
                # le timer stale de l'UI de se déclencher prématurément.
                progress_state.heartbeat()
        except Exception:
            pass

    # Thread de secours : toutes les 60s, refresh updated_at si la campagne est
    # encore "running". Utile quand la génération ne produit aucun log pendant un
    # long silence (ex. : chargement modèle Whisper, encodage ffmpeg silencieux).
    _stop_hb = threading.Event()

    def _heartbeat_loop():
        while not _stop_hb.wait(timeout=60):
            try:
                progress_state.heartbeat()
            except Exception:
                pass

    _hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True, name="hb")
    _hb_thread.start()

    summary = {}
    try:
        if mode == "campaign":
            summary = campaign_run(ct, progress=log_cb)
        elif mode == "generate":
            by_net = eligible_accounts(ct)
            n = sum(len(v) for v in by_net.values()) or 1
            gen_mod.generate(n, ct.gen_type, on_log=logger.info)
        elif mode == "distribute":
            pub = Publisher()
            summary = pub.run(network_ids=list(ct.networks) or None,
                              content_type_id=ct.id, progress=log_cb)
    except Exception as e:
        logger.error("Routine exception : %s", e, exc_info=True)
        summary["error"] = str(e)
    finally:
        _stop_hb.set()
        _hb_thread.join(timeout=5)
        pub_n = summary.get("published", 0)
        fail_n = summary.get("failed", 0)
        cd_n = summary.get("skipped_cooldown", 0)
        reauth = progress_state.read().get("needs_reauth", [])
        parts = [f"{pub_n} publiée(s)"]
        if fail_n:
            parts.append(f"{fail_n} échec(s)")
        if cd_n:
            parts.append(f"{cd_n} en cooldown")
        if reauth:
            parts.append(f"{len(reauth)} compte(s) à ré-authentifier")
        recap = " · ".join(parts)
        status = "error" if (fail_n and not pub_n and not cd_n) else "done"
        progress_state.finish(summary=recap, status=status)

    logger.info("Routine : terminee.")


# ── Entrée ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Routine quotidienne ContentHub")
    parser.add_argument("--now", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    logger.info("=== ContentHub Routine — %s%s ===",
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                " (--now)" if args.now else "")

    params = _resolve_params(cfg)
    run_campaign(params, cfg)


if __name__ == "__main__":
    main()
